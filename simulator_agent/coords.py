def pixels_to_points(px: float, py: float, scale: int = 3) -> tuple[float, float]:
    return px / scale, py / scale


def points_to_pixels(px: float, py: float, scale: int = 3) -> tuple[float, float]:
    return px * scale, py * scale
