"""
Language Switch Agent Demo

This agent starts with Deepgram STT in 'multi' (multilingual) mode and after
a few conversation turns, detects the user's language and switches STT to that
specific language for better accuracy. The switch happens only once.

Features:
- Uses LiveKit Inference for all models (no external API keys needed beyond LiveKit)
- Deepgram Nova-3 STT in multi-language mode initially
- OpenAI GPT-4.1-mini for LLM
- Cartesia Sonic-3 for TTS (multilingual support)
- Automatic language detection via function calling
- One-time switch from multi to specific language
"""

import logging
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
)
from livekit.plugins import noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

load_dotenv(".env.local")

logger = logging.getLogger("lang-switch-agent")
logger.setLevel(logging.INFO)

# Supported languages for Deepgram Nova-3
# Key: language code, Value: (display name, deepgram code, cartesia code)
SUPPORTED_LANGUAGES = {
    "en": ("English", "en", "en"),
    "es": ("Spanish", "es", "es"),
    "fr": ("French", "fr", "fr"),
    "de": ("German", "de", "de"),
    "pt": ("Portuguese", "pt-BR", "pt"),
    "nl": ("Dutch", "nl", "nl"),
    "sv": ("Swedish", "sv", "sv"),
    "da": ("Danish", "da", "da"),
    "ru": ("Russian", "ru", "ru"),
    "it": ("Italian", "it", "it"),
    "pl": ("Polish", "pl", "pl"),
}


class LanguageSwitchAgent(Agent):
    """
    Voice agent that automatically detects and switches to the user's language.
    
    The agent starts in multilingual mode and after detecting the user's language
    through conversation, switches STT to that specific language for better accuracy.
    """
    
    def __init__(self) -> None:
        super().__init__(
            instructions="""You are a helpful multilingual voice assistant.
            
IMPORTANT LANGUAGE DETECTION TASK:
- You are currently listening in multilingual mode (STT set to 'multi')
- After 2-3 conversation turns with the user, you MUST detect their primary language
- Once you're confident about the user's language, call the 'set_detected_language' function
- Only call this function ONCE - it permanently switches the STT to that language
- After switching, continue the conversation naturally in the detected language

CONVERSATION GUIDELINES:
- Be friendly, helpful, and concise in your responses
- Respond in the same language the user speaks
- Don't use complex formatting, emojis, or special characters
- Keep responses natural and conversational for voice

SUPPORTED LANGUAGES:
English (en), Spanish (es), French (fr), German (de), Portuguese (pt),
Dutch (nl), Swedish (sv), Danish (da), Russian (ru), Italian (it), Polish (pl)

If the user speaks a language not in this list, stick with 'multi' mode.""",
        )
        self._language_locked = False
        self._current_language = "multi"
        self._turn_count = 0
    
    async def on_enter(self) -> None:
        """Called when the agent becomes active in the session."""
        await self.session.generate_reply(
            instructions="Greet the user warmly and ask how you can help them today. "
                        "Speak in a neutral way that works for any language."
        )
    
    @function_tool()
    async def set_detected_language(
        self,
        context: RunContext,
        language_code: str,
    ) -> str:
        """Set the detected user language to switch STT from multi to specific language.
        
        Call this function ONCE after 2-3 turns when you're confident about the user's
        primary language. This permanently switches STT to that language for better accuracy.
        
        Args:
            language_code: The ISO 639-1 language code (e.g., 'en', 'es', 'fr', 'de', 'pt', 'nl', 'sv', 'da', 'ru', 'it', 'pl')
        
        Returns:
            Confirmation message about the language switch
        """
        # Prevent multiple switches
        if self._language_locked:
            return f"Language already locked to {self._current_language}. No further switches allowed."
        
        # Validate language code
        if language_code not in SUPPORTED_LANGUAGES:
            available = ", ".join(SUPPORTED_LANGUAGES.keys())
            return f"Unsupported language code '{language_code}'. Supported: {available}"
        
        lang_name, deepgram_code, cartesia_code = SUPPORTED_LANGUAGES[language_code]
        
        logger.info(f"Switching language from 'multi' to '{language_code}' ({lang_name})")
        
        # Update STT to specific language
        if self.session.stt is not None:
            self.session.stt.update_options(language=deepgram_code)
            logger.info(f"STT updated to language: {deepgram_code}")
        
        # Update TTS to specific language
        if self.session.tts is not None:
            self.session.tts.update_options(language=cartesia_code)
            logger.info(f"TTS updated to language: {cartesia_code}")
        
        # Lock the language - no more switches
        self._language_locked = True
        self._current_language = language_code
        
        return f"Language successfully set to {lang_name} ({language_code}). STT switched from 'multi' to '{deepgram_code}' for better accuracy. Continue the conversation in {lang_name}."


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
    
    # Create session with:
    # - Deepgram Nova-3 STT in 'multi' mode (multilingual)
    # - OpenAI GPT-4.1-mini for LLM (via LiveKit Inference)
    # - Cartesia Sonic-3 for TTS (multilingual support)
    session = AgentSession(
        # STT: Start in multilingual mode
        stt=inference.STT(
            model="deepgram/nova-3",
            language="multi",  # Multilingual mode - will be switched later
        ),
        # LLM: Simple and fast model via LiveKit Inference
        llm=inference.LLM(
            model="openai/gpt-4.1-mini",
        ),
        # TTS: Multilingual voice
        tts=inference.TTS(
            model="cartesia/sonic-3",
            voice="9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",  # Jacqueline - confident voice
            language="en",  # Default to English, will be updated when language is detected
        ),
        # VAD for voice activity detection
        vad=ctx.proc.userdata["vad"],
        # Turn detection for better conversation flow
        turn_detection=MultilingualModel(),
    )
    
    # Start the session with our agent
    await session.start(
        room=ctx.room,
        agent=LanguageSwitchAgent(),
        room_options=agents.room_io.RoomOptions(
            audio_input=agents.room_io.AudioInputOptions(
                # Use appropriate noise cancellation based on participant type
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
