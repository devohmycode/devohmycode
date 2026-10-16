"""Convert the supplied portrait to colored and monochrome ASCII; requires Pillow."""

import argparse
import json
from pathlib import Path

from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "assets")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    picture = Image.open(args.source).convert("RGB")
    # shortcut: crop fits this supplied portrait; reframe when replacing the image.
    crop = picture.crop((280, 70, 1100, 1040))
    mask = Image.new("L", crop.size)
    mask.putdata([
        0 if g > r * 1.4 and b > r * 1.4 and g > 90 else 255
        for r, g, b in crop.get_flattened_data()
    ])
    columns, rows = 64, 46
    crop.putalpha(mask)
    sampled = crop.resize((columns, rows), Image.Resampling.LANCZOS)
    visible = sampled.getchannel("A")
    sampled = sampled.convert("RGB")
    colors = sampled.quantize(colors=32, method=Image.Quantize.MEDIANCUT,
                              dither=Image.Dither.NONE).convert("RGB")
    ramp = "ox*#%@"
    text_rows, run_rows = [], []
    for y in range(rows):
        text_row, runs = "", []
        for x in range(columns):
            rgb = colors.getpixel((x, y))
            light = sum(a * b for a, b in zip(rgb, (0.2126, 0.7152, 0.0722))) / 255
            character = ramp[min(len(ramp) - 1, int(light ** 0.65 * len(ramp)))]
            color = "#%02x%02x%02x" % rgb
            if visible.getpixel((x, y)) < 128:
                character, color = " ", "#000000"
            text_row += character
            if runs and runs[-1][0] == color:
                runs[-1][1] += character
            else:
                runs.append([color, character])
        text_rows.append(text_row)
        run_rows.append(runs)

    portrait = {"columns": columns, "height": rows, "cellAspect": 0.6, "rows": run_rows}
    assert len(text_rows) == rows and all(len(row) == columns for row in text_rows)
    assert all(row.isascii() for row in text_rows)
    assert all("".join(run[1] for run in line) == row
               for line, row in zip(run_rows, text_rows))
    assert len({color for line in run_rows for color, text in line if text.strip()}) <= 32
    (args.output / "portrait.json").write_text(json.dumps(portrait, separators=(",", ":")) + "\n", encoding="utf-8")
    (args.output / "portrait.txt").write_text("\n".join(text_rows) + "\n", encoding="ascii")

    original = crop.resize((43, 28), Image.Resampling.LANCZOS)
    original_luminance = original.convert("L")
    original_mask = original.getchannel("A")
    original_ramp = " .:-=+*#%@"
    original_rows = [
        "".join(
            original_ramp[original_luminance.getpixel((x, y)) * 9 // 255]
            if original_mask.getpixel((x, y)) >= 128 else " "
            for x in range(43)
        )
        for y in range(28)
    ]
    assert len(original_rows) == 28 and all(len(row) == 43 and row.isascii() for row in original_rows)
    (args.output / "portrait-original.txt").write_text("\n".join(original_rows) + "\n", encoding="ascii")

    print(f"Created {columns}x{rows} colored and 43x28 monochrome ASCII in {args.output}")


if __name__ == "__main__":
    main()
