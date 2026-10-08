// Confirm before destructive actions: <form data-confirm="...">
document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
        if (!window.confirm(form.dataset.confirm)) {
            event.preventDefault();
        }
    });
});

// Auto-dismiss success alerts after 4 seconds
document.querySelectorAll(".alert-success").forEach((alertEl) => {
    setTimeout(() => {
        bootstrap.Alert.getOrCreateInstance(alertEl).close();
    }, 4000);
});