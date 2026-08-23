# immich-quality-review

`immich-quality-review` is an independent community project for finding technically questionable images in an [Immich](https://immich.app/) library. It is not affiliated with or endorsed by Immich.

> Status: early development / experimental. The project currently provides a package skeleton and quality-provider boundaries, not a working asset-review service.

## Motivation

Large photo libraries often contain blurred, poorly exposed, low-contrast, or low-resolution assets that deserve a second look. This project will use the documented Immich API as its reliable baseline and may optionally accept event hints from compatible Immich v3 Workflows/plugins, then surface review candidates for people to decide on manually.

**Safety principle: review candidates only; never delete assets automatically.**

## Planned core capabilities

- Blur and sharpness signals
- Brightness, underexposure, and overexposure signals
- Contrast and resolution checks
- Initial and incremental asset discovery through the Immich API, with optional workflow/plugin event hints
- A review album or equivalent workflow for flagged candidates
- Optional ML/IQA providers behind a separate provider interface

The initial quality engine will use freely usable, conventional image-analysis techniques. Optional model-based providers remain separate: `pyiqa`, for example, is not a required dependency because of its non-commercial licensing terms.

## Architecture

The first deployment target is a separate Docker worker. The documented Immich API remains the baseline for backfill, asset metadata/content, compatibility fallback, and review synchronization. A thin optional workflow/plugin bridge may provide event-driven intake, but it does not contain quality logic and is not required to run the worker. A FastAPI service or web UI may be added later, without coupling the core worker to either transport or presentation layer.

See [the architecture note](docs/architecture.md) for the intended boundaries.

## Development

Python 3.12 or newer is required.

```powershell
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
```

Docker is planned as the primary deployment method. The bootstrap image is intentionally minimal:

```powershell
docker build -t immich-quality-review .
docker run --rm immich-quality-review
```

## Contributing

Contributions and early design feedback are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md), the [Code of Conduct](CODE_OF_CONDUCT.md), and [SECURITY.md](SECURITY.md) before participating.


<br>
<br>

---

## Donate

<table align="center" width="100%" border="0" bgcolor:=#3f3f3f>
<tr align="center">
<td align="center">
if you prefer a one-time donation

[![donate-Paypal](https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif)](https://paypal.me/FamilieRuhl)

</td>

<td align="center">
Become a patron, by simply clicking on this button (**very appreciated!**):

[![Become a patron](https://c5.patreon.com/external/logo/become_a_patron_button.png)](https://www.patreon.com/join/6555448/checkout?ru=undefined)

</td>
</tr>
</table>

<br>
<br>

---

## Copyright

`2026 (c)Vitaly Ruhl`


## License

This project is licensed under the [GNU Affero General Public License v3.0 only](LICENSE).
