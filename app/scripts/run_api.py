"""Launch the loopback API, optionally serving the built local React UI."""

import argparse

import uvicorn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--serve-ui", action="store_true", help="Serve frontend/dist with the API on loopback.")
    args = parser.parse_args()
    uvicorn.run(
        "app.local_app:create_app" if args.serve_ui else "app.main:app",
        factory=args.serve_ui, host="127.0.0.1", reload=args.reload, port=args.port,
    )


if __name__ == "__main__":
    main()
