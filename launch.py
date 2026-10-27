if __name__ == "__main__":
    try:
        from fluxftp.app import main
        main()
    except Exception:
        # Let the supervisor fall back without a blocking bootloader error dialog.
        from fluxftp.openssl_runtime import _ready_file
        if _ready_file:
            raise SystemExit(1)
        raise
