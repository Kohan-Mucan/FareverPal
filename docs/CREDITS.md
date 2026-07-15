# Credits & third-party attribution

Farever Pal is an **unofficial, community** fan tool. It is not affiliated with
or endorsed by the developers of Farever. Please buy and support the game on
Steam. This tool is taken down, or has any contested content stripped, on
request from the developers.

## The game

- **Farever** and all of its names, data, icons, and assets belong to its
  developers. Any game-derived data or imagery shown here is used only to help
  players read about the game, with attribution, and is removed on request.
- Built on the **[Heaps](https://heaps.io)** engine and the
  **[HashLink](https://hashlink.haxe.org)** VM.

## Other community work

### farever-map — IceCaveBear

The minimap's world→map-image coordinate transform (the scale/offset that maps
in-game `(x, y)` to a pixel on the world map) is **derived from** the community
web map [`IceCaveBear/farever-map`](https://github.com/IceCaveBear/farever-map),
specifically its `map.js`. Full credit and thanks to **IceCaveBear** for working
out that mapping.

> **License note:** at the time of writing, `farever-map` does **not** publish a
> license. Absent a license, the default is "all rights reserved", so reuse of
> its code/derived values is not automatically granted. We use only a small
> derived coordinate formula and credit it here; if IceCaveBear would prefer we
> not use it, or would like different wording, we will change or remove it on
> request. (See `MINIMAP_PROVENANCE` below for the open question about the map
> image itself.)

### MINIMAP_PROVENANCE — open item

The bundled world-map image (`assets/map/W1.png`) needs its source confirmed and
credited or replaced:

- If it was **rendered/produced by farever-map or another community mapper**, it
  must be credited to them and used only with permission.
- If it is **extracted game level art**, it is out of scope for this project's
  own stated asset rules (level art is not meant to be redistributed) and should
  be replaced with an independently produced map or removed.

Until this is resolved, treat the bundled map image as provisional.

## Bundled fonts & icons

- **Inter** — SIL Open Font License 1.1
- **JetBrains Mono** — SIL Open Font License 1.1
- **Lucide** icons — ISC License

Their license texts ship alongside the font/icon files in `assets/`.
