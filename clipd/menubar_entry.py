"""Entry point for the clipd-menubar binary."""


def main():
    from clipd.menubar import main as _main
    _main()


if __name__ == "__main__":
    main()
