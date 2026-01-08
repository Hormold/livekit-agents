"""
Language Switch Agent with Background Observer Pattern

This agent uses a background observer to detect the user's language automatically.
The main agent focuses on its task (collecting feedback) while the observer:
1. Monitors conversation transcripts
2. After 2+ user turns, runs an LLM to detect language with high confidence (99%+)
3. Waits for the right moment to switch STT (when user turn ends)
4. Only switches STT (not TTS) for better transcription accuracy

Key differences from the function tool approach:
- No language detection instructions in main agent's prompt
- Background task runs independently and doesn't interrupt conversation
- LLM-based language detection with confidence scoring
- Seamless switching at conversation boundaries
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

from livekit import agents, rtc
from livekit.agents import (
    AgentServer,
    AgentSession,
    Agent,
    JobContext,
    JobProcess,
    RunContext,
    function_tool,
    inference,
    UserInputTranscribedEvent,
)
from livekit.agents.llm import ChatContext
from livekit.plugins import noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

load_dotenv(".env.local")

logger = logging.getLogger("lang-switch-observer")
logger.setLevel(logging.INFO)


# Supported languages for Deepgram Nova-3
SUPPORTED_LANGUAGES = {
    "en": ("English", "en"),
    "es": ("Spanish", "es"),
    "fr": ("French", "fr"),
    "de": ("German", "de"),
    "pt": ("Portuguese", "pt-BR"),
    "nl": ("Dutch", "nl"),
    "sv": ("Swedish", "sv"),
    "da": ("Danish", "da"),
    "ru": ("Russian", "ru"),
    "it": ("Italian", "it"),
    "pl": ("Polish", "pl"),
}


@dataclass
class LanguageObserverData:
    """State for the language observer."""
    user_turns: list[str] = field(default_factory=list)
    language_detected: bool = False
    detected_language: Optional[str] = None
    pending_switch: bool = False


class FeedbackCollectorAgent(Agent):
    """
    Agent that collects product feedback from users.
    
    This agent's job is to gather feedback - it knows nothing about language detection.
    The background observer handles language detection and STT switching independently.
    """
    
    def __init__(self) -> None:
        super().__init__(
            instructions="""You are a friendly assistant collecting product feedback.

YOUR TASK:
Collect user feedback about their experience using our service.

INFORMATION GATHERING ORDER:
1. Greet the user and ask which product they used
2. Ask what they liked about the product
3. Ask what could be improved
4. Ask them to rate the product from 1 to 10
5. Thank them for their feedback

COMMUNICATION RULES:
- Speak in the same language the user speaks
- Be friendly and attentive
- Don't rush the user
- Ask follow-up questions when needed
- Don't use emojis or complex formatting

IMPORTANT: Don't mention technical details (language settings, etc.) to the user.
Just have a natural conversation and collect feedback.""",
        )
        self._feedback_data = {
            "product": None,
            "likes": None,
            "improvements": None,
            "rating": None,
        }
    
    async def on_enter(self) -> None:
        """Greet the user when the session starts."""
        await self.session.generate_reply(
            instructions="Greet the user briefly and neutrally. "
                        "Say that you're collecting feedback and ask which product "
                        "the user would like to leave feedback about."
        )


async def start_language_observer(
    session: AgentSession,
    observer_llm: inference.LLM,
) -> None:
    """
    Start the background language observer.
    
    This observer monitors the conversation and detects the user's language
    using a separate LLM call. When confident about the language (99%+),
    it switches the STT model at the right moment.
    """
    
    # State for the observer
    state = LanguageObserverData()
    
    # Lock for thread-safe state updates
    state_lock = asyncio.Lock()
    
    async def detect_language_with_llm(transcripts: list[str]) -> tuple[Optional[str], float]:
        """
        Use LLM to detect language from transcripts with confidence score.
        
        Returns:
            Tuple of (language_code, confidence) or (None, 0.0) if detection failed
        """
        if not transcripts:
            return None, 0.0
        
        # Combine transcripts for analysis
        combined_text = "\n".join(f"- {t}" for t in transcripts)
        
        # Create a chat context for language detection
        detection_prompt = f"""Analyze these user messages and determine what language they are speaking.

User messages:
{combined_text}

Supported languages: {', '.join(f'{code} ({name})' for code, (name, _) in SUPPORTED_LANGUAGES.items())}

Respond in JSON format ONLY:
{{"language_code": "xx", "confidence": 0.99, "language_name": "Language Name"}}

CRITICAL RULES:
- confidence must be between 0.0 and 1.0
- Only return a language if the user is speaking COHERENTLY in ONE language
- If the user is mixing multiple languages, return null with 0.0 confidence
- If messages are too short, fragmented, or unclear, return null
- If transcription quality seems poor (gibberish, wrong words), return null
- Only return high confidence (0.95+) when ALL messages are clearly in the same language
- Consider the semantic coherence - are they saying something meaningful in that language?

Return null if:
- User mixes languages (e.g., Russian words + Portuguese words)
- Messages don't form coherent sentences
- Transcription looks like errors or noise
- You're not absolutely certain about the language

{{"language_code": null, "confidence": 0.0, "language_name": null}}
"""
        
        try:
            import json
            
            # Create a simple chat context for detection
            chat_ctx = ChatContext()
            chat_ctx.add_message(role="user", content=detection_prompt)
            
            # Run LLM for detection and collect full response
            response_text = ""
            async with observer_llm.chat(chat_ctx=chat_ctx) as stream:
                async for chunk in stream:
                    # ChatChunk has delta.content
                    if chunk.delta and chunk.delta.content:
                        response_text += chunk.delta.content
            
            logger.debug(f"LLM detection response: {response_text[:200]}")
            
            # Parse JSON response
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1
            if start_idx >= 0 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx]
                result = json.loads(json_str)
                
                lang_code = result.get("language_code")
                confidence = float(result.get("confidence", 0.0))
                lang_name = result.get("language_name", "Unknown")
                
                if lang_code and lang_code in SUPPORTED_LANGUAGES:
                    logger.info(f"🎯 Language detection: {lang_name} ({lang_code}) with {confidence:.0%} confidence")
                    return lang_code, confidence
                    
        except Exception as e:
            logger.error(f"Language detection failed: {e}")
        
        return None, 0.0
    
    async def switch_stt_language(language_code: str) -> None:
        """Switch STT to the detected language."""
        async with state_lock:
            if state.language_detected:
                return  # Already switched
            
            lang_name, deepgram_code = SUPPORTED_LANGUAGES[language_code]
            
            logger.info(f"🔄 Switching STT from 'multi' to '{deepgram_code}' ({lang_name})")
            
            # Update STT only (not TTS)
            if session.stt is not None:
                session.stt.update_options(language=deepgram_code)
                logger.info(f"✅ STT successfully switched to: {deepgram_code}")
            
            state.language_detected = True
            state.detected_language = language_code
            state.pending_switch = False
    
    async def evaluate_and_switch() -> None:
        """Evaluate collected transcripts and switch language if confident enough."""
        async with state_lock:
            if state.language_detected or len(state.user_turns) < 3:
                return  # Already detected or not enough data
            
            transcripts = state.user_turns.copy()
        
        # Run language detection
        lang_code, confidence = await detect_language_with_llm(transcripts)
        
        if lang_code and confidence >= 0.95:
            async with state_lock:
                state.pending_switch = True
            
            # Wait for a good moment to switch (brief pause)
            await asyncio.sleep(0.1)
            
            await switch_stt_language(lang_code)
    
    # Event handler for user transcriptions
    @session.on("user_input_transcribed")
    def on_user_transcribed(event: UserInputTranscribedEvent) -> None:
        """Handle user speech transcriptions."""
        # Only process final transcripts (not interim)
        if not event.is_final:
            return
        
        transcript = event.transcript.strip()
        if not transcript:
            return
        
        # Check if we already detected language
        if state.language_detected:
            return
        
        # Add to user turns
        async def add_turn() -> None:
            async with state_lock:
                state.user_turns.append(transcript)
                turn_count = len(state.user_turns)
            
            logger.info(f"📝 Observer: User turn #{turn_count}: {transcript[:50]}...")
            
            # After 3+ turns, try to detect language (need enough context for coherent speech detection)
            if turn_count >= 3:
                asyncio.create_task(evaluate_and_switch())
        
        asyncio.create_task(add_turn())
    
    logger.info("🔍 Language observer started - monitoring conversation for language detection")


# Create the agent server
server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    """Prewarm function to load VAD model once per process."""
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    """Main entrypoint for the agent session."""
    
    ctx.log_context_fields = {"room": ctx.room.name}
    
    # Create session with STT in multilingual mode
    session = AgentSession(
        # STT: Start in multilingual mode
        stt=inference.STT(
            model="deepgram/nova-3",
            language="multi",  # Will be switched by observer
        ),
        # LLM for main conversation
        llm=inference.LLM(
            model="openai/gpt-4.1-mini",
        ),
        # TTS - stays the same, no language switching
        tts=inference.TTS(
            model="cartesia/sonic-3",
            voice="9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
        ),
        # VAD for voice activity detection
        vad=ctx.proc.userdata["vad"],
        # Turn detection for better conversation flow
        turn_detection=MultilingualModel(),
    )
    
    # Create a separate LLM instance for the observer
    # This can be a different (possibly slower but smarter) model
    observer_llm = inference.LLM(model="openai/gpt-4.1-mini")
    
    # Start the background language observer
    await start_language_observer(session, observer_llm)
    
    # Start the session with our feedback collector agent
    await session.start(
        room=ctx.room,
        agent=FeedbackCollectorAgent(),
        room_options=agents.room_io.RoomOptions(
            audio_input=agents.room_io.AudioInputOptions(
                noise_cancellation=lambda params: (
                    noise_cancellation.BVCTelephony()
                    if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                    else noise_cancellation.BVC()
                ),
            ),
        ),
    )
    
    # Connect to the room
    await ctx.connect()


if __name__ == "__main__":
    agents.cli.run_app(server)
