(() => {
    async function request(path, options = {}) {
        const headers = new Headers(options.headers || {});
        if (options.body !== undefined) headers.set("Content-Type", "application/json");
        const response = await fetch(path, {
            ...options,
            headers,
            credentials: "same-origin",
            cache: "no-store"
        });
        if (response.status === 204) return null;
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            const error = new Error(payload.error || `Request failed (${response.status}).`);
            error.status = response.status;
            throw error;
        }
        return payload;
    }

    window.storeApi = {
        get: (path) => request(path),
        post: (path, body) => request(path, { method: "POST", body: JSON.stringify(body) }),
        patch: (path, body) => request(path, { method: "PATCH", body: JSON.stringify(body) }),
        delete: (path) => request(path, { method: "DELETE" })
    };
})();
