document.getElementById('advantages-toggle-bar').addEventListener('click', function() {
    let advantages = document.getElementById('advantages-container');
    advantages.classList.toggle('collapsed');
});

// JavaScript to handle tab switching logic
document.addEventListener("DOMContentLoaded", function() {
    const tabs = document.querySelectorAll(".tabs");
    const tabContents = document.querySelectorAll(".tab-content");
    const navTabs = document.querySelector(".nav-tabs");

    tabs.forEach(tab => {
        tab.addEventListener("click", function() {
            const targetId = this.id.replace("tab-", "content-");

            tabs.forEach(t => t.classList.remove("active"));
            tabContents.forEach(tc => tc.classList.remove("active"));

            this.classList.add("active");
            document.getElementById(targetId).classList.add("active");

            // Auto-scroll to the selected tab
            tab.scrollIntoView({
                behavior: 'smooth',
                inline: 'center',
                block: 'nearest'
            });
        });
    });

    // Horizontal scrolling with mouse wheel
    navTabs.addEventListener("wheel", function(event) {
        if (event.deltaY !== 0) {
            navTabs.scrollLeft += event.deltaY * 2;  // Adjust scroll speed if needed
            event.preventDefault();  // Prevent vertical scrolling
        }
    });
});