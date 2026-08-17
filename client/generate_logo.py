from PIL import Image

img = Image.open("assets/desktop_logo.png")

img.save(
    "assets/logo.ico",
    sizes=[
        (16, 16),
        (32, 32),
        (48, 48),
        (64, 64),
        (128, 128),
        (256, 256),
    ]
)