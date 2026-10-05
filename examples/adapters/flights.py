"""pi-code adapter for flights-mcp (Google Flights prices via fast-flights).

Searches only read prices, so they run without asking. Results are already
short (one line per flight), so no condense()."""
PREFIX = "flights_"
CONFIRM = False


def summary(name, args):
    route = f"{str(args.get('origin', '?')).upper()}->{str(args.get('destination', '?')).upper()}"
    when = args.get("month") or " -> ".join(filter(None, [args.get("date"), args.get("return_date")]))
    return f"{route} {when}"
