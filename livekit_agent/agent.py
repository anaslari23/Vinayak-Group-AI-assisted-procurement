"""LiveKit voice worker for the procurement agent.
LiveKit does the real-time audio (voice activity detection, streaming speech-to-text and text-to-speech, both from Sarvam, any Indian language).
The BRAIN is the same agent engine as everywhere else: each finished utterance goes to the app's /livekit/turn, which replies in the caller's language.
Run (needs LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET in ../.env):  .venv/bin/python agent.py dev"""
import asyncio, logging, os
import httpx
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
from livekit import agents
from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli
from livekit.plugins import openai, sarvam, silero

APP = os.getenv("APP_URL", "http://127.0.0.1:8000")
HEAD = {"x-bridge-key": os.getenv("WA_BRIDGE_KEY", "")}
GREETING = "नमस्ते, मैं विनायक ग्रुप का प्रोक्योरमेंट असिस्टेंट हूँ। बताइए, किस सामग्री की ज़रूरत है? आप किसी भी भाषा में बोल सकते हैं।"
log = logging.getLogger("procurement-voice")


class ProcurementVoice(Agent):
    """The language model slot is replaced: the reply comes from the procurement engine, which also decides what to ask, confirm and do."""
    def __init__(self, sid: str):
        super().__init__(instructions="You are the voice of the Vinayak procurement agent.")
        self.sid, self.lang = sid, "hi-IN"

    async def llm_node(self, chat_ctx, tools, model_settings):
        text = ""
        for item in reversed(chat_ctx.items):
            if getattr(item, "type", "") == "message" and getattr(item, "role", "") == "user":
                text = (item.text_content or "").strip(); break
        if not text:
            return
        try:
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(f"{APP}/livekit/turn", json={"session": self.sid, "text": text, "lang": self.lang}, headers=HEAD); j = r.json()
        except Exception as e:
            log.warning("turn failed: %r", e); yield "Sorry, I had a problem on my side. Please say that again."; return
        if j.get("lang"):
            self.lang = j["lang"]
            try: self.session.tts.update_options(target_language_code=self.lang)   # answer in the language the caller used
            except Exception: pass
        yield j["reply"]
        if j.get("done"):
            asyncio.create_task(self._hang_up())

    async def _hang_up(self):
        await asyncio.sleep(2)
        self.session.shutdown(drain=True)


async def entrypoint(ctx: JobContext):
    await ctx.connect()
    session = AgentSession(
        stt=sarvam.STT(language="unknown", model="saaras:v4"),           # auto-detects the caller's language
        vad=silero.VAD.load(),
        llm=openai.LLM(api_key="unused"),                                   # never called: llm_node above replaces it
        tts=sarvam.TTS(target_language_code="hi-IN", model="bulbul:v3"),
    )
    agent = ProcurementVoice(ctx.room.name)

    @session.on("user_input_transcribed")
    def _lang(ev):
        if ev.is_final and getattr(ev, "language", None): agent.lang = str(ev.language)

    await session.start(agent=agent, room=ctx.room)
    await session.say(GREETING, allow_interruptions=True)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
