# V2 Deployment Surface

`deploy/v2/configs/base.yaml` adalah baseline config resmi.

Runtime V2 harus dimuat dengan layering:

`base -> profile -> environment -> governed CLI`

Artifacts di folder ini sekarang memegang:

- launcher PowerShell V2 yang memanggil `python -m tsp_v2.run_v2`
- governed profile configs
- deployment-facing config selection surface

Forward-test dan contest profile mengharapkan `news.source_path` ada dan berisi snapshot news yang valid. Kalau file itu tidak tersedia atau stale, preflight memang akan fail loud.

Secrets tetap dilarang masuk YAML dan harus datang dari environment `TSP_V2_*`.

Telemetry runtime V2 menulis structured records ke `telemetry_index` melalui layer persistence yang sudah ada. Event telemetry tetap read-only terhadap keputusan trading.

`python -m tsp_v2.run_v2 start` sekarang sudah memiliki live activation path yang menghubungkan preflight, connect, reconcile, dan activate loop lewat adapter MT5 nyata; mode `--dry-run` tetap mempertahankan validasi chain tanpa menyalakan broker live.
