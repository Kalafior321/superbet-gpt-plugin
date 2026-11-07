import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const BASE = Deno.env.get("SUPABASE_URL") || "";
const SECRET_PUBLIC_KEYS = Deno.env.get("SUPABASE_PUBLISHABLE_KEYS") || "{}";
const PUB = (JSON.parse(SECRET_PUBLIC_KEYS).default as string | undefined)
  || Deno.env.get("SUPABASE_ANON_KEY") || "";
const PAGE = await Deno.readTextFile(new URL("./index.html", import.meta.url));

const common = {
  "Cache-Control": "no-store",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
};

function json(data: Record<string, unknown>, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...common, "Content-Type": "application/json; charset=utf-8" },
  });
}

async function signedIn(token: string): Promise<boolean> {
  if (!BASE || !PUB || !token || token.length > 8000) return false;
  try {
    const res = await fetch(BASE + "/auth/v1/user", {
      headers: { "Authorization": "Bearer " + token, "apikey": PUB },
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return false;
    const user = await res.json();
    return Boolean(user?.id);
  } catch {
    return false;
  }
}

Deno.serve(async (request: Request) => {
  const url = new URL(request.url);
  if (request.method !== "GET") return json({ error: "Method not allowed" }, 405);

  if (url.searchParams.get("api") !== "events") {
    const content = PAGE.replaceAll("__PROJECT_URL__", BASE)
      .replaceAll("__PUBLISHABLE_KEY__", PUB);
    return new Response(content, {
      status: 200,
      headers: {
        ...common,
        "Content-Type": "text/html; charset=utf-8",
        "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'",
      },
    });
  }

  const token = request.headers.get("Authorization")?.match(/^Bearer\s+(.+)$/i)?.[1] || "";
  if (!await signedIn(token)) return json({ error: "Sign-in required" }, 401);

  const day = url.searchParams.get("d") || "";
  const sport = url.searchParams.get("s") || "";
  const league = url.searchParams.get("l") || "";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)
      || !["Soccer", "Ice Hockey", "Basketball"].includes(sport)
      || (league && !/^\d{1,10}$/.test(league))) {
    return json({ error: "Invalid filters" }, 400);
  }

  const params = new URLSearchParams({ d: day, s: sport });
  if (league) params.set("l", league);
  const target = "https://www.thesportsdb.com/api/v1/json/123/eventsday.php?" + params.toString();

  try {
    const upstream = await fetch(target, {
      headers: { "Accept": "application/json" },
      signal: AbortSignal.timeout(12000),
    });
    if (!upstream.ok) return json({ error: "Sports data source unavailable" }, 502);
    const data = await upstream.json();
    const events = Array.isArray(data.events) ? data.events.slice(0, 3) : [];
    return json({ events, source: "TheSportsDB Free", limited: true });
  } catch {
    return json({ error: "Sports data source timed out" }, 504);
  }
});
