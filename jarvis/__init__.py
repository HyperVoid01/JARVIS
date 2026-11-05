"""JARVIS - a local voice assistant.

Stage 1: wake word ("Hey Jarvis") -> record command -> speech-to-text.
Stage 2: local LLM (Ollama) with tool calling.
Stage 3: spoken replies with Piper text-to-speech.
"""

__version__ = "0.3.0"
