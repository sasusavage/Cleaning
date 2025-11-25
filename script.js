document.addEventListener("DOMContentLoaded", function () {
    var prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var animatedElements = document.querySelectorAll("[data-animate]");
    var parallaxElements = document.querySelectorAll("[data-depth]");
    var nav = document.querySelector(".nav");
    var navToggle = document.querySelector(".nav__toggle");
    var primaryNav = document.getElementById("primary-nav");
    var navItems = document.querySelectorAll(".nav__item[data-section]");
    var preloader = document.getElementById("preloader");
    var body = document.body;
    var yearSpan = document.getElementById("year");
    var form = document.getElementById("quote-form");
    var feedback = document.querySelector(".form-feedback");

    if (yearSpan) {
        yearSpan.textContent = new Date().getFullYear();
    }

    if (animatedElements.length) {
        if (prefersReducedMotion) {
            animatedElements.forEach(function (element) {
                element.classList.add("is-visible");
            });
        } else {
            var revealObserver = new IntersectionObserver(function (entries, observer) {
                entries.forEach(function (entry) {
                    if (entry.isIntersecting) {
                        entry.target.classList.add("is-visible");
                        observer.unobserve(entry.target);
                    }
                });
            }, {
                threshold: 0.25,
                rootMargin: "0px 0px -10% 0px"
            });

            animatedElements.forEach(function (element) {
                revealObserver.observe(element);
            });
        }
    }

    var updateNavState = function () {
        if (!nav) {
            return;
        }
        var sticky = window.scrollY > 32;
        nav.classList.toggle("is-sticky", sticky);
    };

    updateNavState();
    window.addEventListener("scroll", updateNavState, { passive: true });

    // Handle navigation active state
    navItems.forEach(function (item) {
        item.addEventListener("click", function (event) {
            event.preventDefault();
            var section = item.getAttribute("data-section");
            var sectionEl = document.getElementById(section);

            // Remove active class from all items
            navItems.forEach(function (nav) {
                nav.classList.remove("is-active");
            });

            // Add active class to clicked item
            item.classList.add("is-active");

            // Scroll to section if it exists
            if (sectionEl) {
                sectionEl.scrollIntoView({ behavior: "smooth" });
            }
        });
    });

    // Set initial active state based on current scroll position
    var updateActiveNav = function () {
        var scrollPosition = window.scrollY + 100;
        var currentSection = null;

        navItems.forEach(function (item) {
            var section = item.getAttribute("data-section");
            var sectionEl = document.getElementById(section);
            if (sectionEl) {
                if (sectionEl.offsetTop <= scrollPosition) {
                    currentSection = section;
                }
            }
        });

        navItems.forEach(function (item) {
            item.classList.remove("is-active");
        });

        if (currentSection) {
            var activeItem = document.querySelector("[data-section='" + currentSection + "']");
            if (activeItem) {
                activeItem.classList.add("is-active");
            }
        }
    };

    window.addEventListener("scroll", updateActiveNav, { passive: true });
    updateActiveNav();

    // Mobile nav toggle
    if (navToggle && nav) {
        navToggle.addEventListener("click", function () {
            var isOpen = nav.classList.toggle("nav--open");
            navToggle.setAttribute("aria-expanded", String(isOpen));
        });

        // Close menu when a link is clicked
        if (primaryNav) {
            primaryNav.addEventListener("click", function (e) {
                var target = e.target;
                if (target && target.closest && target.closest(".nav__item")) {
                    nav.classList.remove("nav--open");
                    navToggle.setAttribute("aria-expanded", "false");
                }
            });
        }

        // Close on Escape
        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape") {
                nav.classList.remove("nav--open");
                if (navToggle) navToggle.setAttribute("aria-expanded", "false");
            }
        });
    }

    if (parallaxElements.length && !prefersReducedMotion) {
        var state = { x: 0, y: 0, targetX: 0, targetY: 0 };

        var lerp = function (start, end, amount) {
            return (1 - amount) * start + amount * end;
        };

        var pointerHandler = function (event) {
            var innerWidth = window.innerWidth;
            var innerHeight = window.innerHeight;
            var x = event.clientX / innerWidth - 0.5;
            var y = event.clientY / innerHeight - 0.5;
            state.targetX = x * 2;
            state.targetY = y * 2;
        };

        var animateParallax = function () {
            state.x = lerp(state.x, state.targetX, 0.08);
            state.y = lerp(state.y, state.targetY, 0.08);

            parallaxElements.forEach(function (element) {
                var depth = parseFloat(element.getAttribute("data-depth") || "0");
                element.style.transform = "translate3d(" + (state.x * depth * 30) + "px, " + (state.y * depth * 30) + "px, 0)";
            });

            window.requestAnimationFrame(animateParallax);
        };

        window.addEventListener("pointermove", pointerHandler);
        animateParallax();
    }

    if (preloader) {
        window.addEventListener("load", function () {
            var finalizePreloader = function () {
                body.classList.add("is-loaded");
                preloader.classList.add("is-hidden");
                var removePreloader = function () {
                    preloader.remove();
                };
                preloader.addEventListener("transitionend", removePreloader, { once: true });
                window.setTimeout(removePreloader, 900);
            };

            if (prefersReducedMotion) {
                finalizePreloader();
            } else {
                window.setTimeout(finalizePreloader, 280);
            }
        });
    } else {
        body.classList.add("is-loaded");
    }

    if (form && feedback) {
        form.addEventListener("submit", function (event) {
            event.preventDefault();

            var name = form.elements.name.value.trim();
            var phone = form.elements.phone.value.trim();
            var service = form.elements.service.value;
            var errorMessage = "";

            if (!name) {
                errorMessage = "Please enter your full name.";
            } else if (!phone || phone.replace(/[^0-9]/g, "").length < 6) {
                errorMessage = "Please provide a valid phone number.";
            } else if (!service) {
                errorMessage = "Please choose the service you need.";
            }

            feedback.classList.remove("is-error", "is-success");

            if (errorMessage) {
                feedback.textContent = errorMessage;
                feedback.classList.add("is-error");
                return;
            }

            feedback.textContent = "Thank you! We'll call you back within 24 hours.";
            feedback.classList.add("is-success");
            form.reset();
        });
    }

    // Job Application Modal
    var jobModal = document.getElementById("job-modal");
    var jobButtons = document.querySelectorAll("[data-job]");
    var jobTitlePlaceholder = document.getElementById("job-title-placeholder");
    var jobPositionInput = document.getElementById("job-position");
    var closeModalElements = document.querySelectorAll("[data-close-modal]");
    var jobForm = document.getElementById("job-application-form");

    if (jobModal && jobButtons.length) {
        var openModal = function (jobTitle) {
            if (jobTitlePlaceholder) jobTitlePlaceholder.textContent = jobTitle;
            if (jobPositionInput) jobPositionInput.value = jobTitle;
            jobModal.classList.add("is-open");
            jobModal.setAttribute("aria-hidden", "false");
            document.body.style.overflow = "hidden"; // Prevent background scrolling
        };

        var closeModal = function () {
            jobModal.classList.remove("is-open");
            jobModal.setAttribute("aria-hidden", "true");
            document.body.style.overflow = "";
            if (jobForm) jobForm.reset();
            var feedback = jobForm.querySelector(".form-feedback");
            if (feedback) {
                feedback.textContent = "";
                feedback.className = "form-feedback";
            }
        };

        jobButtons.forEach(function (button) {
            button.addEventListener("click", function (e) {
                e.preventDefault();
                var jobTitle = button.getAttribute("data-job");
                openModal(jobTitle);
            });
        });

        closeModalElements.forEach(function (el) {
            el.addEventListener("click", closeModal);
        });

        // Close on Escape key
        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape" && jobModal.classList.contains("is-open")) {
                closeModal();
            }
        });

        // Handle form submission
        if (jobForm) {
            jobForm.addEventListener("submit", function (e) {
                e.preventDefault();
                var feedback = jobForm.querySelector(".form-feedback");
                if (!feedback) return;

                // Simulate submission
                var btn = jobForm.querySelector("button[type='submit']");
                var originalText = btn.textContent;
                btn.textContent = "Sending...";
                btn.disabled = true;

                setTimeout(function () {
                    feedback.textContent = "Application sent successfully! We'll be in touch.";
                    feedback.classList.add("is-success");
                    btn.textContent = originalText;
                    btn.disabled = false;
                    
                    setTimeout(closeModal, 2000);
                }, 1500);
            });
        }
    }

    // --- BACK TO TOP BUTTON ---
    var backToTopBtn = document.getElementById("backToTop");

    if (backToTopBtn) {
        window.addEventListener("scroll", function () {
            if (window.scrollY > 500) {
                backToTopBtn.classList.add("is-visible");
            } else {
                backToTopBtn.classList.remove("is-visible");
            }
        }, { passive: true });

        backToTopBtn.addEventListener("click", function (e) {
            e.preventDefault();
            window.scrollTo({
                top: 0,
                behavior: "smooth"
            });
        });
    }
});
