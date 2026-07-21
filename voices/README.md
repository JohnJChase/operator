# Piper voices

Default operator voice: `hfc_female` →
`voices/hfc_female/en_US-hfc_female-medium.onnx`

The `.onnx` weights are gitignored (~61 MB). Install with:

```bash
just setup-voices
```

That copies from `~/we302-first-prototype/voices/hfc_female/` when present.
Otherwise download the Rhasspy `en_US/hfc_female/medium` voice and place the
`.onnx` and `.onnx.json` beside each other in this directory.
