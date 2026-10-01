(() => {
    const statusClass = (kind) => kind === "success" ? "store-message-success" : "store-message-error";

    function showMessage(element, message, kind = "error") {
        if (!element) return;
        let target = element;
        if (!element.matches?.(".store-message")) {
            target = element.querySelector?.(".store-message") || document.createElement("p");
            if (!target.parentElement) {
                target.className = "store-message";
                element.prepend(target);
            }
        }
        target.className = `store-message ${statusClass(kind)}`;
        target.textContent = message;
        target.hidden = false;
    }

    function returnPath() {
        const candidate = new URLSearchParams(location.search).get("next");
        if (!candidate) return "index.html";
        try {
            const url = new URL(candidate, location.origin);
            if (url.origin !== location.origin || /(?:login|Sign)\.html$/i.test(url.pathname)) return "index.html";
            return `${url.pathname}${url.search}${url.hash}`;
        } catch {
            return "index.html";
        }
    }

    let activeUser = null;
    let readyResolve;
    const ready = new Promise((resolve) => { readyResolve = resolve; });

    function updateNavigation(user) {
        document.querySelectorAll(".admin-dashboard-button").forEach((button) => {
            button.hidden = user?.role !== "admin";
        });
        document.querySelectorAll(".auth-site-links, .catalog-nav-links, .store-nav > ul").forEach((list) => {
            const loginLink = list.querySelector('a[href="login.html"], a[data-auth-link="login"]');
            const signupLink = list.querySelector('a[href="Sign.html"], a[data-auth-link="signup"]');
            if (user) {
                if (loginLink) {
                    loginLink.href = "cart.html";
                    loginLink.textContent = "My bookings";
                    loginLink.dataset.authLink = "login";
                }
                if (signupLink) {
                    signupLink.href = "#sign-out";
                    signupLink.textContent = "Sign out";
                    signupLink.dataset.authLink = "signup";
                }
                if (user.role === "admin" && !list.querySelector('a[href="admin.html"]')) {
                    const link = document.createElement("a");
                    link.href = "admin.html";
                    link.textContent = "Admin";
                    const item = document.createElement("li");
                    item.className = "admin-nav-item";
                    item.append(link);
                    list.append(item);
                }
            } else {
                if (loginLink) {
                    loginLink.href = "login.html";
                    loginLink.textContent = "Login";
                    delete loginLink.dataset.authLink;
                }
                if (signupLink) {
                    signupLink.href = "Sign.html";
                    signupLink.textContent = "Sign Up";
                    delete signupLink.dataset.authLink;
                }
                list.querySelector(".admin-nav-item")?.remove();
            }
        });
    }

    async function refresh() {
        try {
            const result = await window.storeApi.get("/api/session");
            activeUser = result.user;
            updateNavigation(activeUser);
            return activeUser;
        } finally {
            readyResolve(activeUser);
        }
    }

    window.StoreAuth = {
        ready,
        get user() { return activeUser; },
        showMessage,
        async refresh() { return refresh(); },
        async requireUser() {
            await ready;
            if (!activeUser) {
                const next = `${location.pathname}${location.search}${location.hash}`;
                location.href = `login.html?next=${encodeURIComponent(next)}`;
                return null;
            }
            return activeUser;
        },
        async signOut() {
            try { await window.storeApi.post("/api/auth/logout", {}); } finally {
                activeUser = null;
                location.href = "index.html";
            }
        }
    };

    document.addEventListener("click", (event) => {
        const signOutLink = event.target.closest('a[href="#sign-out"]');
        if (!signOutLink) return;
        event.preventDefault();
        window.StoreAuth.signOut();
    });

    document.addEventListener("DOMContentLoaded", async () => {
        try {
            await refresh();
        } catch (error) {
            const status = document.querySelector("[data-server-status]") || document.querySelector("main");
            showMessage(status, error.message || "The local server is not responding. Start it with python server.py.");
            readyResolve(null);
        }

        const loginForm = document.getElementById("login-form");
        loginForm?.addEventListener("submit", async (event) => {
            event.preventDefault();
            const button = loginForm.querySelector('[type="submit"]');
            button.disabled = true;
            try {
                await window.storeApi.post("/api/auth/login", {
                    email: loginForm.elements.email.value.trim(),
                    password: loginForm.elements.password.value
                });
                location.href = returnPath();
            } catch (error) {
                showMessage(document.getElementById("auth-message"), error.message);
            } finally {
                button.disabled = false;
            }
        });

        const registerForm = document.getElementById("register-form");
        registerForm?.addEventListener("submit", async (event) => {
            event.preventDefault();
            const password = registerForm.elements.password.value;
            if (password !== registerForm.elements.confirmPassword.value) {
                showMessage(document.getElementById("auth-message"), "The passwords do not match.");
                return;
            }
            const button = registerForm.querySelector('[type="submit"]');
            button.disabled = true;
            try {
                await window.storeApi.post("/api/auth/signup", {
                    fullName: registerForm.elements.fullName.value.trim(),
                    email: registerForm.elements.email.value.trim(),
                    password
                });
                location.href = returnPath();
            } catch (error) {
                showMessage(document.getElementById("auth-message"), error.message);
            } finally {
                button.disabled = false;
            }
        });
    });
})();
