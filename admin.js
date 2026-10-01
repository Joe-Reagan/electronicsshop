(() => {
    const message = document.getElementById("admin-message");
    const statusOptions = ["new", "contacted", "confirmed", "completed", "cancelled"];
    const money = (value) => `KSh ${Number(value || 0).toLocaleString("en-KE")}`;

    function notify(text, kind = "error") {
        window.StoreAuth.showMessage(message, text, kind);
    }

    function appendText(parent, tag, className, value) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        element.textContent = value;
        parent.append(element);
        return element;
    }

    async function loadProducts() {
        const { products } = await window.storeApi.get("/api/admin/products");
        const list = document.getElementById("admin-products");
        document.getElementById("product-count").textContent = `${products.length} total`;
        list.replaceChildren();
        if (!products.length) return appendText(list, "p", "admin-empty", "No products found.");
        products.forEach((product) => {
            const row = document.createElement("article");
            row.className = "admin-product-row";
            const image = document.createElement("img");
            image.src = product.image_url || "cart.png";
            image.alt = "";
            const info = document.createElement("div");
            appendText(info, "h3", "", product.name);
            appendText(info, "p", "", `${product.category} · ${money(product.price_ksh)}${product.is_active ? "" : " · hidden"}`);
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "admin-delete";
            remove.textContent = "Delete";
            remove.addEventListener("click", async () => {
                if (!window.confirm(`Delete ${product.name}? Existing bookings are retained.`)) return;
                remove.disabled = true;
                try {
                    await window.storeApi.delete(`/api/admin/products/${product.id}`);
                    notify("Product deleted.", "success");
                    await loadProducts();
                } catch (error) {
                    notify(error.message);
                    remove.disabled = false;
                }
            });
            row.append(image, info, remove);
            list.append(row);
        });
    }

    async function loadBookings() {
        const { bookings } = await window.storeApi.get("/api/bookings");
        const list = document.getElementById("admin-bookings");
        document.getElementById("booking-count").textContent = `${bookings.length} requests`;
        list.replaceChildren();
        if (!bookings.length) return appendText(list, "p", "admin-empty", "There are no bookings yet.");
        bookings.forEach((booking) => {
            const card = document.createElement("article");
            card.className = "admin-booking";
            const header = document.createElement("div");
            header.className = "admin-booking-head";
            const heading = document.createElement("div");
            appendText(heading, "h3", "", booking.product_name);
            appendText(heading, "p", "booking-meta", `${booking.quantity} × ${money(booking.price_each_ksh)} · ${new Date(booking.created_at).toLocaleString()}`);
            const select = document.createElement("select");
            select.setAttribute("aria-label", `Booking status for ${booking.product_name}`);
            statusOptions.forEach((status) => {
                const option = document.createElement("option");
                option.value = status;
                option.textContent = status[0].toUpperCase() + status.slice(1);
                option.selected = booking.status === status;
                select.append(option);
            });
            select.addEventListener("change", async () => {
                select.disabled = true;
                try {
                    await window.storeApi.patch(`/api/admin/bookings/${booking.id}`, { status: select.value });
                    notify(`Booking marked ${select.value}.`, "success");
                } catch (error) {
                    notify(error.message);
                    select.value = booking.status;
                } finally {
                    select.disabled = false;
                }
            });
            header.append(heading, select);
            const contact = document.createElement("div");
            contact.className = "booking-contact";
            [
                `Customer: ${booking.customer_name}`,
                `Email: ${booking.customer_email}`,
                `Phone: ${booking.phone}`,
                `Delivery: ${booking.delivery_address}`,
                ...(booking.requested_date ? [`Requested date: ${booking.requested_date}`] : []),
                ...(booking.notes ? [`Notes: ${booking.notes}`] : [])
            ].forEach((value) => appendText(contact, "p", "booking-meta", value));
            card.append(header, contact);
            list.append(card);
        });
    }

    document.addEventListener("DOMContentLoaded", async () => {
        document.getElementById("admin-sign-out")?.addEventListener("click", () => window.StoreAuth.signOut());
        try {
            await window.StoreAuth.ready;
            const user = await window.StoreAuth.requireUser();
            if (!user) return;
            if (user.role !== "admin") {
                notify("This page is restricted to store administrators.");
                document.querySelectorAll("#product-form input, #product-form textarea, #product-form button").forEach((control) => { control.disabled = true; });
                return;
            }

            const form = document.getElementById("product-form");
            form.addEventListener("submit", async (event) => {
                event.preventDefault();
                const button = form.querySelector('[type="submit"]');
                button.disabled = true;
                const fields = new FormData(form);
                try {
                    await window.storeApi.post("/api/admin/products", {
                        name: String(fields.get("name") || "").trim(),
                        category: String(fields.get("category") || "").trim(),
                        description: String(fields.get("description") || "").trim(),
                        imageUrl: String(fields.get("image_url") || "").trim(),
                        priceKsh: Number(fields.get("price")),
                        isFlashSale: fields.has("is_flash_sale"),
                        isNewArrival: fields.has("is_new_arrival")
                    });
                    form.reset();
                    notify("Product added to the live catalog.", "success");
                    await loadProducts();
                } catch (error) {
                    notify(error.message);
                } finally {
                    button.disabled = false;
                }
            });

            const transferForm = document.getElementById("admin-transfer-form");
            transferForm?.addEventListener("submit", async (event) => {
                event.preventDefault();
                if (!window.confirm("This immediately removes your admin access and gives it to the registered account. Continue?")) return;
                const button = transferForm.querySelector('[type="submit"]');
                button.disabled = true;
                try {
                    const fields = new FormData(transferForm);
                    await window.storeApi.post("/api/admin/transfer", {
                        email: String(fields.get("email") || "").trim(),
                        password: String(fields.get("password") || ""),
                        confirmation: String(fields.get("confirmation") || "")
                    });
                    await window.StoreAuth.signOut();
                } catch (error) {
                    notify(error.message || "Ownership transfer failed.");
                    button.disabled = false;
                }
            });
            await Promise.all([loadProducts(), loadBookings()]);
        } catch (error) {
            notify(error.message || "Could not load the admin dashboard.");
        }
    });
})();
