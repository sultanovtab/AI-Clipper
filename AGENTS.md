# AI Clipper - Project Rules

## Platform
- Windows-first local application.
- Project path: D:\AI-Clipper
- Use the existing .venv for Python dependencies.
- Prefer simple, stable, free/open-source dependencies.

## Large media
- Local videos may be 100-400+ GB.
- NEVER upload a local video to a cloud service.
- NEVER copy an entire local video into the project.
- NEVER POST a huge local file through the browser UI.
- Access local media directly from its original filesystem path.
- Local source files must be treated as read-only.

## Local file selection
- Browser UI must use a backend-triggered native Windows file picker.
- Return only the selected filesystem path to the application.
- Do not use a browser file upload for large local media.

## URL sources
- Support YouTube, Twitch and TikTok through yt-dlp.
- During early phases query metadata only.
- Do not download a full high-quality VOD unless explicitly needed later.

## AI / privacy
- Do not use Experiential yet unless the current phase explicitly asks for it.
- Never hardcode API keys.
- Full video/audio must never be sent to an external LLM.

## Development
- Work only on the requested phase.
- Do not implement future phases early.
- Keep dependencies minimal.
- Do not introduce React/Node/frontend frameworks unless genuinely necessary.
- Prefer FastAPI + lightweight HTML/CSS/JavaScript.
- Test functionality before claiming it works.
- Fix errors encountered during the requested phase.
