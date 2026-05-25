# ASRS — AI Systemic Risk Score

## What this is
A composite risk scoring algorithm that measures systemic economic risk from the AI valuation cycle. Produces a 0-100 score from 5 weighted modules, each with 5 data-driven sub-metrics.

## Current state
- `ASRS_Tool.html`: standalone HTML tool with all scoring logic and UI

## What we're building toward
- `data_fetcher.py`: pulls live values from FRED, Polygon.io, SEC EDGAR
- `data/asrs_data.json`: output file the HTML reads on load
- GitHub Actions workflow to run the fetcher weekly

## Architecture
- Scoring logic stays in JavaScript inside the HTML file
- Python handles data fetching only, writes to `asrs_data.json`
- No database — flat JSON file is sufficient
- FRED API is free, no key needed
- Polygon.io requires API key — store in `.env` as `POLYGON_API_KEY`
- Never commit `.env` to git
