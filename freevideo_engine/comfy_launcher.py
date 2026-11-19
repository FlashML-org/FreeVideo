"""Entry point for the desktop launcher."""


def main():
    from .modern_launcher import main as launch
    launch()


if __name__ == '__main__':
    main()
