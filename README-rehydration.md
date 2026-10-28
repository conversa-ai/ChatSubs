# OpenSubtitles rehydration for esCorpiusDialog 1.3

The new files named below are MIT licensed by LICENSE-hydration-MIT.txt.
Pre-existing extraction code and the local untracked licence are not relicensed.
The exact historical function snapshot is built locally from the retained source;
its licence scope remains separate and is recorded in the release provenance.

Place the downloaded opensubtitles-historical-v2.zip in this working directory:

```bash
unzip opensubtitles-historical-v2.zip
python3 prepare_historical_recipe.py --source extractDialogOpenSubtitles.py --output local/historical/retained-functions.py
python3 rehydrate_opensubtitles.py --manifest opensubtitles-historical-v2/manifest.jsonl --recipe opensubtitles-historical-v2/recipe.json --language ca --max-files 2 --dry-run
python3 -m unittest discover -s tests_hydration -v
```

CPython 3.11 and the standard library suffice. Use --snapshot local/historical/retained-functions.py when executing recovery. Read the deposited REHYDRATION.md for API/environment credential handling, source-local mode, exact list gates, resume, quotas and concrete bounded commands. API credentials use OPENSUBTITLES_API_KEY plus OPENSUBTITLES_USERNAME/OPENSUBTITLES_PASSWORD, or OPENSUBTITLES_TOKEN. Never put secrets in Git or public logs. All modes default to zero-network plans; --execute enables the chosen source operation. Local policy can reserve source connections for a human operator.

Historical IDSubtitleFile, IDSubtitle, modern record IDs and modern files[].file_id are separate. Only an exact supported group/language/disc mapping can reach the API download operation; only a matching complete ordered-list count and fingerprint can attach published positions. Unavailable, ambiguous and mismatching cases remain explicit. The prior 40-case cached check is historical evidence, not a universal recovery guarantee.
