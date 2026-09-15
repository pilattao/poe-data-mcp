"""Strict game selection shared by the crafting/environment adapters."""
import os


def selected_game() -> str:
    game = os.environ.get('POE_GAME', 'poe2').strip().lower()
    if game not in {'poe1', 'poe2'}:
        raise ValueError(f'Unsupported POE_GAME={game!r}; select poe2 or poe1 explicitly')
    return game
