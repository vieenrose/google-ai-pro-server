#!/usr/bin/env python3
"""
cli.py — Gemini chat + Deep Research demo, powered by your Google AI Pro
subscription (no Gemini API key).

Quick start:
    python3 cli.py                          # interactive REPL
    python3 cli.py "hello"                  # one-shot chat
    python3 cli.py --deep "quantum computing trends 2026"   # deep research
    python3 cli.py --backend agy            # force a specific backend

Backends (auto-selected: agy > direct > gemini > mock):
    agy      official Antigravity CLI — sign in once with your AI Pro Google
             account (`agy`), then headless calls use the subscription.
    direct   same credentials, but talks to Google's backend directly
             (community technique) — no process spawns.
    gemini   legacy Gemini CLI (deprecated for Google One users).
    mock     offline simulator so the demo runs anywhere.

REPL commands:
    /deep <topic>    run the Deep Research workflow (plan → search → report)
    /model <name>    switch model (gemini-3.6-flash, gemini-3.1-pro, …)
    /backend <name>  switch backend (agy, direct, gemini, mock)
    /stream          toggle streaming output
    /status          show active backend + model + availability
    /help            this help
    /quit            exit
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time

from gemini_backends import (AgyBackend, ChatResult, DirectTokenBackend,
                             GeminiCliBackend, MockBackend, build_prompt,
                             pick_backend)
from deep_research import run_research

# ── ANSI colours (no dependencies) ────────────────────────────────────────────
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_CYAN = "\033[36m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"
C_MAGENTA = "\033[35m"
C_BLUE = "\033[34m"

MODELS = [
    "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.1-pro",
    "gemini-3.1-pro-high", "gemini-2.5-pro", "claude-sonnet-4.6",
    "claude-opus-4.6", "gpt-oss-120b",
]


def banner() -> str:
    lines = [
        f"{C_BOLD}{C_CYAN}┌────────────────────────────────────────────────────────┐{C_RESET}",
        f"{C_BOLD}{C_CYAN}│  Gemini AI Pro demo — chat & Deep Research            │{C_RESET}",
        f"{C_BOLD}{C_CYAN}│  powered by your Google AI Pro subscription (no API)  │{C_RESET}",
        f"{C_BOLD}{C_CYAN}└────────────────────────────────────────────────────────┘{C_RESET}",
    ]
    return "\n".join(lines)


def backend_status(backend) -> str:
    ok = backend.available()
    state = f"{C_GREEN}available{C_RESET}" if ok else f"{C_RED}NOT available{C_RESET}"
    return f"{C_DIM}{backend.describe()}{C_RESET}  [{state}]"


def print_help() -> None:
    doc = __doc__.split("REPL commands:")[1]
    print(doc)


def chat_once(backend, prompt: str, model: str, stream: bool) -> ChatResult:
    if stream:
        print(f"{C_DIM}streaming…{C_RESET}")
        out = []
        for delta, meta in backend.chat_stream(prompt, model):
            if meta.get("error"):
                print(f"\n{C_RED}error: {meta['error']}{C_RESET}")
                return ChatResult(text="", error=meta["error"])
            out.append(delta)
            print(delta, end="", flush=True)
        print()
        return ChatResult(text="".join(out), model=model)
    result = backend.chat(prompt, model=model)
    return result


def deep_research_cmd(backend, topic: str, model: str, max_questions: int) -> None:
    live = getattr(backend, "name", "") in ("agy", "gemini")
    tag = "live web research (Google Search grounded)" if live else \
          "knowledge-based (approximation — live search needs the agy backend)"
    print(f"\n{C_BOLD}{C_MAGENTA}Deep Research workflow{C_RESET} — {tag}\n")

    def progress(msg: str) -> None:
        print(f"  {C_YELLOW}▸{C_RESET} {msg}", flush=True)

    t0 = time.time()
    result = run_research(backend, topic, max_questions=max_questions, model=model, progress=progress)
    elapsed = time.time() - t0

    if result.error:
        print(f"\n{C_RED}Deep Research failed: {result.error}{C_RESET}")
        return

    n_q = len(result.plan)
    print(f"\n{C_BOLD}{C_GREEN}✔ Report complete — {n_q} question(s), {len(result.sources)} source(s), {elapsed:.0f}s{C_RESET}\n")
    print(f"{C_DIM}{'─' * 72}{C_RESET}")
    print(result.report)
    if result.sources:
        print(f"\n{C_BOLD}{C_CYAN}Sources ({len(result.sources)}):{C_RESET}")
        for i, url in enumerate(result.sources[:15], 1):
            print(f"  [{i}] {C_DIM}{url}{C_RESET}")
    print(f"\n{C_DIM}plan questions: {len(result.plan)} · phases run: {result.phases_run} · "
          f"duration: {elapsed:.0f}s{C_RESET}")


def repl(backend, model: str, stream: bool, max_questions: int) -> None:
    history: list[dict] = []
    print(banner())
    print(backend_status(backend))
    print(f"{C_DIM}type a message, or /help for commands{C_RESET}\n")

    while True:
        try:
            line = input(f"{C_BOLD}{C_GREEN}you>{C_RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{C_DIM}bye{C_RESET}")
            break
        if not line:
            continue

        if line.startswith("/"):
            cmd, _, arg = line.partition(" ")
            arg = arg.strip()
            if cmd in ("/quit", "/exit", "/q"):
                print(f"{C_DIM}bye{C_RESET}")
                break
            elif cmd == "/help":
                print_help()
            elif cmd == "/deep":
                if not arg:
                    print(f"{C_YELLOW}usage: /deep <topic>{C_RESET}")
                else:
                    deep_research_cmd(backend, arg, model, max_questions)
            elif cmd == "/model":
                if not arg:
                    print(f"{C_DIM}models: {', '.join(MODELS)}{C_RESET}")
                else:
                    model = arg
                    print(f"{C_DIM}model → {model}{C_RESET}")
            elif cmd == "/backend":
                if not arg:
                    print(backend_status(backend))
                else:
                    backend = pick_backend(arg)
                    print(backend_status(backend))
            elif cmd == "/stream":
                stream = not stream
                print(f"{C_DIM}streaming {'ON' if stream else 'OFF'}{C_RESET}")
            elif cmd == "/status":
                print(backend_status(backend))
                print(f"{C_DIM}model: {model} · streaming: {stream}{C_RESET}")
            else:
                print(f"{C_YELLOW}unknown command: {cmd} — try /help{C_RESET}")
            continue

        history.append({"role": "user", "content": line})
        prompt = build_prompt(history)
        result = chat_once(backend, prompt, model, stream)
        if result.error:
            print(f"{C_RED}error: {result.error}{C_RESET}")
        else:
            if not stream:
                print(f"{C_BOLD}{C_CYAN}gemini>{C_RESET} {result.text}")
            history.append({"role": "assistant", "content": result.text})
            usage = result.usage
            if usage:
                print(f"{C_DIM}· {usage.get('total_tokens', '')} tokens{C_RESET}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Gemini AI Pro demo — chat + Deep Research (no API key)")
    parser.add_argument("prompt", nargs="?", help="one-shot prompt (omit for interactive REPL)")
    parser.add_argument("--deep", action="store_true", help="run the Deep Research workflow on the prompt")
    parser.add_argument("--model", default="gemini-3.5-flash", help=f"model (default gemini-3.5-flash; options: {', '.join(MODELS)})")
    parser.add_argument("--backend", choices=["agy", "direct", "gemini", "mock"], help="force a backend")
    parser.add_argument("--no-stream", action="store_true", help="disable streaming (default: on)")
    parser.add_argument("--max-questions", type=int, default=3, help="max research questions (default 3)")
    args = parser.parse_args()

    stream = not args.no_stream
    backend = pick_backend(args.backend)

    if not args.prompt:
        repl(backend, args.model, stream, args.max_questions)
        return 0

    if args.deep:
        deep_research_cmd(backend, args.prompt, args.model, args.max_questions)
    else:
        print(banner())
        print(backend_status(backend))
        result = chat_once(backend, args.prompt, args.model, stream)
        if result.error:
            print(f"{C_RED}error: {result.error}{C_RESET}")
        elif not stream:
            print(f"{C_BOLD}{C_CYAN}gemini>{C_RESET} {result.text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
