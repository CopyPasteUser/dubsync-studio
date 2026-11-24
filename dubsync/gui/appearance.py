"""Shared semantic appearance tokens."""
PALETTES = {
    "light": dict(canvas="#ffffff", surface="#f5f5f7", raised="#e9e9ed",
                  border="#c8c8ce", text="#202124", muted="#575b63",
                  accent="#075bc5", ink="#ffffff", selection="#d6e5fc",
                  warning="#855100", danger="#b4232b", success="#176b3a",
                  thumb="#8a8b92", hover="#e0e1e6", disabled="#777982"),
    "dark": dict(canvas="#1c1c1e", surface="#252528", raised="#333337",
                 border="#505057", text="#f3f3f5", muted="#b9b9c2",
                 accent="#81b8ff", ink="#15253c", selection="#294567",
                 warning="#f2c078", danger="#ff9b9e", success="#84d6a4",
                 thumb="#777780", hover="#414148", disabled="#96969e"),
}


def normalize_appearance(value: object) -> str:
    return value if isinstance(value, str) and value in PALETTES else "light"


def appearance_colors(appearance: object = "light", high_contrast: bool = False) -> dict[str, str]:
    mode = normalize_appearance(appearance)
    colors = dict(PALETTES[mode])
    if high_contrast:
        if mode == "light":
            colors.update(canvas="#ffffff", surface="#ffffff", raised="#eeeeee",
                          border="#343434", text="#000000", muted="#333333",
                          thumb="#424242", disabled="#595959")
        else:
            colors.update(canvas="#080808", surface="#111111", raised="#242424",
                          border="#bcbcbc", text="#ffffff", muted="#e5e5e5",
                          thumb="#bcbcbc", disabled="#b5b5b5")
    return colors
