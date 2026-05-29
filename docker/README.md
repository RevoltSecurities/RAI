# Docker Setup

This image is built from the repository root and uses `docker/Dockerfile`.

## Build

```bash
docker build -f docker/Dockerfile -t revolt-rai:local .
```

## Run

```bash
docker run --rm -it \
  -p 8000:8000 \
  -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  revolt-rai:local
```

`rai chat` starts by default inside the container.

## Notes

- The image installs the full `revolt-rai` package, including the HTTP stack needed by `rai chat`.
- The build copies `static/` from the repository root into the image, so packaged assets are available at runtime.
- If you want a different model or agent, pass the normal `rai` flags through `docker run`, for example:

```bash
docker run --rm -it revolt-rai:local rai chat --agent coder --model openai:gpt-4o
```
