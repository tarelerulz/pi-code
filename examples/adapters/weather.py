"""pi-code tool adapter for weather-mcp's weather_get_weather_summary.

Moved out of pi-code 2026-10-04 so pi-code itself has no tool-specific code.
An adapter is a .py file in ~/.config/pi-code/adapters/ (PI_CODE_ADAPTERS);
pi-code reads these names from it, all optional:

  TOOLS                  tool names this adapter handles (or PREFIX = "weather_")
  CONFIRM                True/False for all its tools, or a list of names that
                         need "run? [Y/n]" first (no adapter says = ask)
  summary(name, args)    one-line display of a call (None = show the args)
  schema(tool)           shorter tool definition to send (skipped with
                         PI_CODE_FULL_TOOL_DOCS=1); runs after pi-code's
                         generic first-sentence trim
  fix_args(name, args)   repair the model's arguments before the call
  condense(name, text)   shrink the tool's result before the model reads it
"""
import json
import os
import re

PREFIX = "weather_"
CONFIRM = False          # only reads a weather service

LOCATIONS = os.path.expanduser(
    os.environ.get("PI_CODE_WEATHER_LOCATIONS", "~/.weather-mcp/locations.json"))
KEEP = ("location_name", "city_name", "days")


def schema(tool):
    """Only place + days: the 14 options (wind in knots, pressure units...)
    were ~3.5k chars, ~50 s of reading on the Pi for every request."""
    fn = tool["function"]
    if fn["name"] != "weather_get_weather_summary":
        return tool
    fn["description"] = ("Weather now and forecast for a saved place (location_name, "
                         "like \"home\") or any city (city_name).")
    params = fn.get("parameters") or {}
    props = {k: v for k, v in (params.get("properties") or {}).items() if k in KEEP}
    params["properties"] = props
    params["required"] = [r for r in params.get("required", []) if r in props]
    return tool


def fix_args(name, args):
    """Send a city_name that names a saved weather-mcp location as location_name,
    and a location_name that is not a saved place as city_name.

    weather-mcp only checks saved places for location_name; a city_name goes
    straight to the geocoder. Small models ask for city_name="home", which
    geocoded to Home, Washington instead of the saved home (2026-09-29).
    """
    try:
        with open(LOCATIONS) as f:
            saved = json.load(f)
    except (OSError, json.JSONDecodeError):
        return args
    loc = args.get("location_name")
    if isinstance(loc, str):
        # the other way round: location_name="Spokane" is not a saved place,
        # so geocode it as a city (seen 2026-10-04 with the quick preset).
        # The model also sends both, location_name="Spokane" + city_name=
        # "Spokane": weather-mcp tries location_name first and fails.
        known = {a.lower() for a in saved} | {n.lower().strip() for p in saved.values()
                                              for n in p.get("alternateNames", [])}
        if loc.lower().strip() not in known:
            args = {k: v for k, v in args.items() if k != "location_name"}
            args.setdefault("city_name", loc)
            return args
    city = args.get("city_name")
    if not isinstance(city, str):
        return args
    want = city.lower().strip()
    for alias, place in saved.items():
        names = [alias.lower()] + [n.lower().strip() for n in place.get("alternateNames", [])]
        if want in names:
            args = {k: v for k, v in args.items() if k != "city_name"}
            args["location_name"] = alias
            break
    return args


def _section(text, title):
    start = text.find(f"# {title}")
    if start < 0:
        return ""
    end = text.find("\n# ", start + 2)
    return text[start:end if end > 0 else len(text)]


def _field(block, name):
    m = re.search(rf"\*\*{name}:\*\*\s*(.+)", block)
    return m.group(1).strip() if m else ""


def condense(name, raw):
    """weather_get_weather_summary markdown (~2.7k chars) -> a few short lines.

    Same facts the model needs: current reading (and its age warning), one
    line per forecast period, alerts. Unknown formats are returned unchanged."""
    if name != "weather_get_weather_summary" or not raw.startswith("# Weather Summary"):
        return raw
    lines = [f"Place: {_field(raw, 'Location')}"]
    cur = _section(raw, "Current Weather Conditions")
    if cur:
        now = [f"{f.lower()} {_field(cur, f)}" for f in
               ("Temperature", "Conditions", "Humidity", "Wind") if _field(cur, f)]
        lines.append("Now: " + ", ".join(now))
        # "34.1 hours old", "2 days old"... (only hours were matched until 2026-10-04,
        # so a 2-day-old reading lost its warning and was reported as current)
        old = re.search(r"observation is (.+?) old", cur)
        if old:
            lines.append(f"WARNING: the 'Now' reading is {old.group(1)} old (station may be down)"
                         " - not the current weather")
    fc = _section(raw, "Weather Forecast")
    for block in re.split(r"^## ", fc, flags=re.M)[1:]:
        period = block.split("\n", 1)[0].strip()
        temp = _field(block, "Temperature")
        sky = _field(block, "Forecast") or _field(block, "Conditions")
        rain = _field(block, "Precipitation Chance")
        lines.append(f"{period}: {temp}, {sky}" + (f", rain {rain}" if rain and rain != "0%" else ""))
    al = _section(raw, "Weather Alerts")
    if "No active weather alerts" in al:
        lines.append("Alerts: none")
    elif al:
        events = re.findall(r"\*\*([^*]+)\*\*", al)
        events = [e for e in events if not e.endswith(":") and "alert" not in e.lower()]
        lines.append("Alerts: " + (", ".join(dict.fromkeys(events)) or "see full report"))
    return "\n".join(lines) if len(lines) > 2 else raw
