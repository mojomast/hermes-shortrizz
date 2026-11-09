/** ctx.rest client. Paths stay inside this plugin's namespace. */

export function createApi(rest) {
  function call(path, options) {
    if (typeof rest !== "function") {
      return Promise.reject(new Error("Studio backend is not connected. Enable the Shortrizz plugin, then Rescan."));
    }
    return Promise.resolve(rest(path, options || {})).then((body) => {
      if (!body || typeof body !== "object") {
        throw new Error("Studio returned an empty response.");
      }
      if (body.ok === false) {
        const error = new Error(body.error || "Studio could not finish that.");
        error.fix = body.fix || "";
        throw error;
      }
      return body;
    });
  }

  return {
    health: () => call("/health"),
    runs: () => call("/runs"),
    run: (id) => call("/runs/" + encodeURIComponent(id)),
    media: (id, rel) => call("/media?run=" + encodeURIComponent(id) + "&path=" + encodeURIComponent(rel)),
    create: (body) => call("/runs", { method: "POST", body }),
    save: (id, body) => call("/runs/" + encodeURIComponent(id) + "/picks", { method: "POST", body }),
    restore: (id, revision) => call("/runs/" + encodeURIComponent(id) + "/restore", { method: "POST", body: { revision } }),
    preview: (id, pickId) => call("/runs/" + encodeURIComponent(id) + "/preview", { method: "POST", body: { pick_id: pickId } }),
    render: (id, picks) => call("/runs/" + encodeURIComponent(id) + "/render", { method: "POST", body: picks ? { picks, snap: true } : {} }),
    restyle: (id, style) => call("/runs/" + encodeURIComponent(id) + "/restyle", { method: "POST", body: { style } }),
    repick: (id) => call("/runs/" + encodeURIComponent(id) + "/repick", { method: "POST", body: {} }),
    open: (id) => call("/runs/" + encodeURIComponent(id) + "/open", { method: "POST", body: {} }),
  };
}

export function blobUrlFromBase64(data, mime) {
  if (typeof data !== "string" || !data) return "";
  if (typeof atob !== "function" || typeof Blob === "undefined" || typeof URL === "undefined" || !URL.createObjectURL) {
    return "";
  }
  const binary = atob(data);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return URL.createObjectURL(new Blob([bytes], { type: mime || "application/octet-stream" }));
}

export function errorText(error) {
  const message = (error && error.message) || "Something went wrong.";
  const fix = error && error.fix;
  return fix ? message + " " + fix : message;
}
