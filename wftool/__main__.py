"""Command line: python -m wftool <command>

  run [--host 0.0.0.0] [--port 8080]   start the app and the background scheduler (emails, reminders, import)
  import                               run the CSV import once and print the result
  init                                 create the database and the default admin (admin/admin)
  backup <file>                        consistent copy of the database while the app runs
"""

import argparse
import logging


def main(argv=None):
    parser = argparse.ArgumentParser(prog="wftool")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--host", default="0.0.0.0")
    run.add_argument("--port", type=int, default=8080)
    sub.add_parser("import")
    sub.add_parser("init")
    backup = sub.add_parser("backup")
    backup.add_argument("file")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from .web import create_app
    app = create_app()
    ext = app.extensions["wftool"]

    if args.command == "init":
        print(f"Database ready in {app.config['DATA_DIR']}. Sign in as admin / admin and change the password.")
    elif args.command == "import":
        from .imports import run_folder
        results = run_folder(ext["db"], app.config["IMPORT_DIR"])
        if not results:
            print(f"No CSV files in {app.config['IMPORT_DIR']}")
        for r in results:
            print(f"{r['file']}: {r['status']} added={r['added']} changed={r['changed']} "
                  f"deactivated={r['deactivated']} skipped_rows={len(r['rejected_rows'])} {r['message'] or ''}")
    elif args.command == "backup":
        ext["db"].backup(args.file)
        print(f"Database copied to {args.file}. Back up the key file separately.")
    elif args.command == "run":
        from .scheduler import Scheduler
        Scheduler(ext["db"], app.config["IMPORT_DIR"], engine=ext["engine"], cipher=ext["cipher"],
                  files=ext["files"]).start()
        try:
            from waitress import serve
        except ImportError:  # development: Flask's own server
            app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)
        else:
            print(f"WFTOOL on http://{args.host}:{args.port} (waitress)", flush=True)
            serve(app, host=args.host, port=args.port, threads=8, ident="WFTOOL")


if __name__ == "__main__":
    main()
