# Experiment readiness

Experiments may begin only when every **blocking** requirement for the selected
profile is `READY`. Optional developer capabilities do not block every
experiment class.

```text
elh experiment-readiness --json
elh experiment-readiness --profile base-inference
```

The command inspects. It does not refresh the fleet, restart workers, publish
records, or repair launchers.

## Profiles

| Profile | Blocking layers |
| --- | --- |
| `base-inference` | Control, Harness, Fabric controller, fleet, workers, models, Commons consumer, artifact write |
| `code-analysis` | base-inference + Joern + Forge |
| `multi-agent` | base-inference + routing |
| `MNEL` | multi-agent + Commons operator publication + reference-studies |
| `RAVEL` | base-inference + reference-studies + Forge |

Joern unavailable degrades code-analysis experiments. It does not block
`base-inference`.

## Fabric compatibility

```text
MIN_SUPPORTED_FABRIC          0.2.0a17 / 6285f7d3f49994e926aa0468a6cc2b644f9a3e85
EXPERIMENT_CERTIFIED_FABRIC   0.2.0a28 / 4f657c4d0441073902ebcbae823c11af43c09535
FABRIC_MAIN_CANARY            main
```

`main` is a forward-compatibility canary only. It is not experiment provenance.

## Claim boundary

A readiness smoke run is `infrastructure validation`. It is not a research
result and must not be cited as MNCS scientific evidence.

## Local fallback

Experiment-mode runs must not silently execute on the controller host. If
`fallback_to_local` is used, the readiness report records that it is explicit.
