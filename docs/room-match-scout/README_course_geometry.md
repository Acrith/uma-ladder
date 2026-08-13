# course_geometry.json

Corner / straight / slope positions per `race_instance_id`, in metres.

## Why this file exists separately

Everything else about a race is recoverable from the scenario blob
every capture carries — frames, results, skill events, the lot. Course
geometry is the **one exception**: it lives on `Gallop.RaceManager` and
only while a race or replay is actually loaded in the client. A capture
taken from the plain result screen has no geometry at all.

That makes these values genuinely irreplaceable without going back into
the game, which is why they are extracted here rather than left implicit
inside a 850 KB capture payload.

## Shape

```json
{
  "800095": {
    "distance": 1600,
    "runners_seen": 11,
    "source_capture": "captures/34408987_800095.withevents.json",
    "straights": [{"start": 200, "end": 720, "front_type": "AcrossFront"}, ...],
    "corners":   [{"start": 720, "end": 995, "number": 3, "is_final": false}, ...],
    "slopes":    [{"start": 520, "end": 695, "slope_type": "Up"}, ...]
  }
}
```

`front_type`: `Front` = the home (finishing) straight, `AcrossFront` =
the back stretch. `distance` is derived as the furthest segment end,
which is the finish line — telemetry runs past it because runners do.

## Covered so far

| race_instance_id | distance | straights | corners | slopes |
|---|---|---|---|---|
| 800095 | 1600 m | 2 | 2 | 2 |
| 800074 | 2200 m | 2 | 4 | 4 |
| 800072 | 3600 m | 5 | 8 | 7 |

## The obvious next step

Nothing reads this file yet — `replay_series` still takes geometry from
whatever the capture happened to carry. Seeding it into the database as
a lookup keyed by `race_instance_id` would mean **any** capture of a
known venue renders the full course profile, with no need to have had a
replay open. Growing the table is then just a matter of capturing each
venue once with a replay loaded.
