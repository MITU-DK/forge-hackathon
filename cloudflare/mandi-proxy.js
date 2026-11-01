// Cloudflare Worker: forwards only data.gov.in resource requests (the mandi price API).
// Needed because data.gov.in refuses connections from some cloud hosts (e.g. Render). No data is stored or changed.
export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (request.method !== "GET" || !/^\/resource\/[0-9a-f-]{36}$/.test(url.pathname)) {
      return new Response("not found", { status: 404 });
    }
    const upstream = "https://api.data.gov.in" + url.pathname + url.search;
    const r = await fetch(upstream, { headers: { Accept: "application/json" } });
    return new Response(r.body, { status: r.status, headers: { "Content-Type": r.headers.get("Content-Type") || "application/json" } });
  },
};
