const html = document.documentElement
const topbar = document.getElementById("layout-topbar")
if (topbar) {
    const handleScroll = () =>
        topbar.setAttribute("data-at-top", window.scrollY < 30 ? "true" : "false")

    window.addEventListener("scroll", handleScroll, { passive: true })
    handleScroll()
}

document.querySelectorAll("[data-theme-control]").forEach((control) => {
    control.addEventListener("click", () => {
        const theme = control.getAttribute("data-theme-control")
        if (theme === "system") {
            html.removeAttribute("data-theme")
        } else {
            html.setAttribute("data-theme", theme)
        }
    })
})
