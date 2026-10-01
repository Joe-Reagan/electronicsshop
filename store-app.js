(() => {
    const money = (amount) => `KSh ${Number(amount || 0).toLocaleString("en-KE")}`;
    const slug = (value) => String(value || "products").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "products";

    function makeProductCard(product, catalog = false) {
        const article = document.createElement("article");
        article.className = catalog ? "product catalog-product" : "product";
        article.dataset.productId = product.id;
        if (catalog && product.is_flash_sale) {
            const badge = document.createElement("span");
            badge.className = "product-badge deal-badge";
            badge.textContent = "Flash pick";
            article.append(badge);
        } else if (catalog && product.is_new_arrival) {
            const badge = document.createElement("span");
            badge.className = "product-badge new-badge";
            badge.textContent = "New";
            article.append(badge);
        }
        const image = document.createElement("img");
        image.src = product.image_url || "cart.png";
        image.alt = `${product.name} product photo`;
        image.loading = "lazy";
        article.append(image);
        if (catalog) {
            const category = document.createElement("span");
            category.className = "product-type";
            category.textContent = product.category;
            article.append(category);
        }
        const name = document.createElement("h3");
        name.textContent = product.name;
        const description = document.createElement("p");
        description.textContent = product.description || "Contact us to learn more about this product.";
        const price = document.createElement("p");
        price.className = "product-price";
        const amount = document.createElement("strong");
        amount.textContent = money(product.price_ksh);
        const each = document.createElement("span");
        each.textContent = "each";
        price.append(amount, each);
        article.append(name, description);
        const buttons = document.createElement("div");
        buttons.className = "btns";
        const book = document.createElement("button");
        book.type = "button";
        book.className = "book-product";
        book.dataset.productId = product.id;
        book.setAttribute("aria-label", `Book ${product.name}`);
        book.textContent = catalog ? "Book" : "Book this product";
        buttons.append(book);
        if (catalog) {
            const foot = document.createElement("div");
            foot.className = "product-foot";
            const note = document.createElement("span");
            note.className = "offer-note";
            note.textContent = product.is_flash_sale ? "Limited-time pick" : product.is_new_arrival ? "Recently added" : "Available to book";
            const actions = document.createElement("div");
            actions.className = "product-foot-actions";
            actions.append(price, buttons);
            foot.append(note, actions);
            article.append(foot);
        } else {
            article.append(price, buttons);
        }
        return article;
    }

    function addCategoryLink(list, label, href) {
        const item = document.createElement("li");
        const link = document.createElement("a");
        link.href = href;
        link.textContent = label;
        item.append(link);
        list.append(item);
    }

    function renderIndexCatalog(products) {
        const content = document.querySelector(".content");
        if (!content) return;
        const groups = new Map();
        products.forEach((product) => {
            const category = product.category || "Other";
            if (!groups.has(category)) groups.set(category, []);
            groups.get(category).push(product);
        });
        content.replaceChildren();
        groups.forEach((items, category) => {
            const normalized = category.toLowerCase();
            const section = document.createElement("section");
            section.id = normalized.includes("computer") || normalized.includes("laptop") ? "laptops"
                : normalized.includes("smartphone") || normalized.includes("phone") ? "smartphones"
                    : `products-${slug(category)}`;
            const grid = document.createElement("div");
            grid.className = "all";
            const heading = document.createElement("h2");
            heading.textContent = category;
            grid.append(heading);
            items.forEach((product) => grid.append(makeProductCard(product)));
            section.append(grid);
            content.append(section);
        });

        const list = document.querySelector(".category > ul");
        if (!list) return;
        list.replaceChildren();
        [["Top deals", "flash.html"], ["Flash sales", "flash.html"], ["New arrivals", "New.html"]]
            .forEach(([label, href]) => addCategoryLink(list, label, href));
        groups.forEach((_products, category) => {
            const normalized = category.toLowerCase();
            const target = normalized.includes("computer") || normalized.includes("laptop") ? "#laptops"
                : normalized.includes("smartphone") || normalized.includes("phone") ? "#smartphones"
                    : `#products-${slug(category)}`;
            addCategoryLink(list, category, target);
        });
    }

    async function loadProducts() {
        let path = "/api/products";
        if (document.body.dataset.catalog === "flash") path += "?flash=1";
        if (document.body.dataset.catalog === "new") path += "?new=1";
        const { products } = await window.storeApi.get(path);
        if (document.body.classList.contains("catalog-page")) {
            const grid = document.querySelector(".catalog-grid");
            if (!grid) return;
            grid.replaceChildren();
            products.forEach((product) => grid.append(makeProductCard(product, true)));
            if (!products.length) {
                const empty = document.createElement("p");
                empty.className = "catalog-empty";
                empty.textContent = "No products are listed here yet. Check back soon.";
                grid.append(empty);
            }
        } else {
            renderIndexCatalog(products);
        }
    }

    async function setupBooking() {
        const dialog = document.getElementById("booking-dialog");
        const form = document.getElementById("booking-form");
        if (!dialog || !form) return;
        let selectedProductId = null;
        document.addEventListener("click", async (event) => {
            const button = event.target.closest(".book-product");
            if (!button) return;
            try {
                const user = await window.StoreAuth.requireUser();
                if (!user) return;
                selectedProductId = button.dataset.productId;
                const { products } = await window.storeApi.get("/api/products");
                const product = products.find((item) => String(item.id) === selectedProductId);
                if (!product) {
                    window.StoreAuth.showMessage(document.querySelector("main"), "This product is no longer available.");
                    return;
                }
                document.getElementById("booking-product-name").textContent = `${product.name} · ${money(product.price_ksh)} each`;
                form.reset();
                form.elements.quantity.value = "1";
                form.elements.email.value = user.email;
                form.elements.customerName.value = user.fullName;
                dialog.showModal();
            } catch (error) {
                window.StoreAuth.showMessage(document.querySelector("main"), error.message || "Could not load this product.");
            }
        });

        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (!await window.StoreAuth.requireUser() || !selectedProductId) return;
            const submit = form.querySelector('[type="submit"]');
            submit.disabled = true;
            try {
                await window.storeApi.post("/api/bookings", {
                    productId: Number(selectedProductId),
                    quantity: Number(form.elements.quantity.value),
                    customerName: form.elements.customerName.value.trim(),
                    phone: form.elements.phone.value.trim(),
                    address: form.elements.address.value.trim(),
                    requestedDate: form.elements.deliveryDate.value,
                    notes: form.elements.notes.value.trim()
                });
                dialog.close();
                location.href = "cart.html?booking=saved";
            } catch (error) {
                window.StoreAuth.showMessage(form.querySelector("#booking-message"), error.message);
            } finally {
                submit.disabled = false;
            }
        });
        ["booking-close", "booking-cancel"].forEach((id) => document.getElementById(id)?.addEventListener("click", () => dialog.close()));
        dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
    }

    async function renderBookings() {
        const list = document.getElementById("cart-items");
        if (!list) return;
        const user = await window.StoreAuth.requireUser();
        if (!user) return;
        const { bookings } = await window.storeApi.get("/api/bookings");
        const empty = document.getElementById("empty-state");
        const totalQuantity = bookings.reduce((sum, item) => sum + item.quantity, 0);
        const total = bookings.reduce((sum, item) => sum + item.price_each_ksh * item.quantity, 0);
        document.getElementById("item-count").textContent = `(${bookings.length})`;
        document.getElementById("selection-count").textContent = String(totalQuantity);
        document.getElementById("cart-total").textContent = money(total);
        empty.hidden = bookings.length > 0;
        list.replaceChildren();
        bookings.forEach((booking) => {
            const row = document.createElement("article");
            row.className = "cart-item";
            const imageBox = document.createElement("div");
            imageBox.className = "item-image";
            const image = document.createElement("img");
            image.src = booking.image_url || "cart.png";
            image.alt = `${booking.product_name} product photo`;
            image.loading = "lazy";
            imageBox.append(image);
            const details = document.createElement("div");
            details.className = "item-details";
            const title = document.createElement("h3");
            title.textContent = booking.product_name;
            const caption = document.createElement("p");
            caption.className = "item-caption";
            caption.textContent = `Booking · ${booking.category} · ${booking.status}`;
            const price = document.createElement("p");
            price.className = "item-price";
            price.textContent = `${money(booking.price_each_ksh)} each · ${money(booking.price_each_ksh * booking.quantity)} subtotal`;
            const customer = document.createElement("div");
            customer.className = "booking-cart-details";
            [
                `Customer: ${booking.customer_name}`,
                `Email: ${booking.customer_email}`,
                `Phone: ${booking.phone}`,
                `Delivery address: ${booking.delivery_address}`,
                ...(booking.requested_date ? [`Requested date: ${booking.requested_date}`] : []),
                ...(booking.notes ? [`Notes: ${booking.notes}`] : [])
            ].forEach((text) => {
                const line = document.createElement("p");
                line.textContent = text;
                customer.append(line);
            });
            details.append(title, caption, price, customer);
            const count = document.createElement("span");
            count.className = "item-quantity";
            count.textContent = `${booking.quantity} ${booking.quantity === 1 ? "item" : "items"}`;
            row.append(imageBox, details, count);
            list.append(row);
        });
        if (new URLSearchParams(location.search).get("booking") === "saved") {
            window.StoreAuth.showMessage(document.querySelector(".page-heading"), "Booking sent. The admin will contact you to follow up.", "success");
        }
    }

    document.addEventListener("DOMContentLoaded", async () => {
        try {
            await window.StoreAuth.ready;
            await loadProducts();
            await setupBooking();
            await renderBookings();
        } catch (error) {
            window.StoreAuth.showMessage(document.querySelector("main"), error.message || "Could not load the store. Is the local server running?");
        }
    });
})();
