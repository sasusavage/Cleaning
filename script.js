document.addEventListener("DOMContentLoaded", function () {
    var prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var animatedElements = document.querySelectorAll("[data-animate]");
    var parallaxElements = document.querySelectorAll("[data-depth]");
    var nav = document.querySelector(".nav");
    var navToggle = document.querySelector(".nav__toggle");
    var primaryNav = document.getElementById("primary-nav");
    var navItems = document.querySelectorAll(".nav__item[data-section]");
    var servicesDropdown = document.querySelector("[data-nav-dropdown]");
    var servicesDropdownToggle = document.querySelector("[data-nav-dropdown-toggle]");
    var preloader = document.getElementById("preloader");
    var body = document.body;
    var yearSpan = document.getElementById("year");
    var serviceGrid = document.querySelector("[data-services-grid]");
    var serviceDrawerGrid = document.getElementById("services-drawer-grid");
    var servicesDrawer = document.getElementById("services-drawer");
    var serviceDrawerToggle = document.querySelector('[data-drawer-toggle="services-drawer"]');
    var servicesEmptyState = document.getElementById("services-empty");
    var rawCatalog = Array.isArray(window.SERVICE_CATALOG) ? window.SERVICE_CATALOG : [];
    var homeSectionOrder = Array.isArray(window.HOME_SECTION_ORDER) ? window.HOME_SECTION_ORDER : [];
    var askForPostcode = Boolean(window.ASK_FOR_POSTCODE);
    var TRAVEL_QUOTE_KEY = "travel_quote_v1";
    var TRAVEL_POSTCODE_KEY = "travel_postcode_v1";
    var currencyFormatter;
    try {
        currencyFormatter = new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP", minimumFractionDigits: 2 });
    } catch (error) {
        currencyFormatter = null;
    }

    var applyHomepageSectionOrder = function () {
        if (!homeSectionOrder.length) {
            return;
        }

        var mainEl = document.querySelector("main");
        if (!mainEl) {
            return;
        }

        var sectionNodes = Array.prototype.slice.call(mainEl.querySelectorAll("[data-home-section]"));
        if (!sectionNodes.length) {
            return;
        }

        var sectionMap = {};
        sectionNodes.forEach(function (node) {
            var key = node.getAttribute("data-home-section");
            if (key) {
                sectionMap[key] = node;
            }
        });

        var orderedNodes = [];
        homeSectionOrder.forEach(function (key) {
            var sectionNode = sectionMap[key];
            if (sectionNode && orderedNodes.indexOf(sectionNode) === -1) {
                orderedNodes.push(sectionNode);
            }
        });

        sectionNodes.forEach(function (node) {
            if (orderedNodes.indexOf(node) === -1) {
                orderedNodes.push(node);
            }
        });

        orderedNodes.forEach(function (node) {
            mainEl.appendChild(node);
        });
    };

    applyHomepageSectionOrder();

    var setStoredTravelQuote = function (quote, postcode) {
        try {
            if (quote) {
                sessionStorage.setItem(TRAVEL_QUOTE_KEY, JSON.stringify(quote));
            } else {
                sessionStorage.removeItem(TRAVEL_QUOTE_KEY);
            }
            if (postcode) {
                sessionStorage.setItem(TRAVEL_POSTCODE_KEY, postcode);
            } else if (!quote) {
                sessionStorage.removeItem(TRAVEL_POSTCODE_KEY);
            }
        } catch (error) {
            console.warn("Unable to persist travel quote", error);
        }
    };

    var getStoredTravelQuote = function () {
        try {
            var stored = sessionStorage.getItem(TRAVEL_QUOTE_KEY);
            return stored ? JSON.parse(stored) : null;
        } catch (error) {
            console.warn("Unable to read travel quote", error);
            return null;
        }
    };

    var getStoredPostcode = function () {
        try {
            return sessionStorage.getItem(TRAVEL_POSTCODE_KEY) || "";
        } catch (error) {
            return "";
        }
    };

    var normalizePostcodeValue = function (value) {
        return (value || "").toString().trim().toUpperCase().replace(/\s+/g, "");
    };

    var normalizePriceValue = function (value) {
        if (value === null || value === undefined || value === "") {
            return null;
        }
        var numeric = Number(value);
        return Number.isNaN(numeric) ? null : numeric;
    };

    var normalizePaymentOptionValue = function (value) {
        var normalized = String(value || "").trim().toLowerCase().replace(/[-\s]+/g, "_");
        if (["prebook_save", "prebook", "pay_now", "stripe", "card"].indexOf(normalized) !== -1) {
            return "prebook_save";
        }
        return "pay_in_person";
    };

    var escapeHtml = function (text) {
        if (!text) return "";
        var div = document.createElement("div");
        div.textContent = String(text);
        return div.innerHTML;
    };

    var deriveStartingPrice = function (service) {
        if (!service) return null;
        var candidates = [];
        if (Array.isArray(service.pricing) && service.pricing.length) {
            candidates = candidates.concat(service.pricing.map(function (option) { return normalizePriceValue(option.price); }));
        }
        if (Array.isArray(service.pricing_items)) {
            candidates = candidates.concat(service.pricing_items.map(function (item) { return normalizePriceValue(item.price); }));
        }
        if (Array.isArray(service.tenancy_rates)) {
            service.tenancy_rates.forEach(function (rate) {
                candidates.push(normalizePriceValue(rate.standard_price));
                candidates.push(normalizePriceValue(rate.deep_clean_price));
            });
        }
        if (Array.isArray(service.pricing_tiers)) {
            candidates = candidates.concat(service.pricing_tiers.map(function (tier) { return normalizePriceValue(tier.hourly_rate); }));
        }
        candidates = candidates.filter(function (v) { return v !== null; });
        if (!candidates.length) return null;
        return Math.min.apply(Math, candidates);
    };

        var normalizeService = function (service) {
        if (!service || typeof service !== "object") {
            return null;
        }
        var options = Array.isArray(service.pricing) ? service.pricing : (Array.isArray(service.options) ? service.options : []);
        var pricing = options.map(function (option) {
            var priceValue = normalizePriceValue(option && (option.price !== undefined ? option.price : option.option_price));
            return {
                id: option && (option.id !== undefined ? option.id : option.option_id),
                label: option && (option.label || option.option_label || ""),
                details: option && option.details ? option.details : "",
                price: priceValue
            };
        }).filter(function (option) { return option && option.id !== undefined && option.id !== null; });

        var normalized = {
            id: service.id,
            name: service.name || service.title || "",
            description: service.description || "",
            short_description: service.short_description || service.description || "",
            service_category: service.service_category || "one_time",
            image: service.image || service.image_path || "",
            pricing: pricing,
            pricing_items: Array.isArray(service.pricing_items) ? service.pricing_items : [],
            pricing_tiers: Array.isArray(service.pricing_tiers) ? service.pricing_tiers : [],
            tenancy_rates: Array.isArray(service.tenancy_rates) ? service.tenancy_rates : [],
            discount_threshold: normalizePriceValue(service.discount_threshold),
            discount_percent: normalizePriceValue(service.discount_percent),
            pricing_type: service.pricing_type || null,
            table_header_col1: service.table_header_col1 || 'Property Type',
            table_header_col2: service.table_header_col2 || 'Standard Price',
            table_header_col3: service.table_header_col3 || 'Upgrade Option',
            allow_multiselect: service.allow_multiselect || 0
        };

        normalized.startingPrice = deriveStartingPrice(normalized);

        return normalized;
    };

    var SERVICE_CATALOG = rawCatalog.map(normalizeService).filter(Boolean);

    var formatPrice = function (value) {
        if (typeof value !== "number" || Number.isNaN(value)) {
            return "Custom quote";
        }
        return currencyFormatter ? currencyFormatter.format(value) : "£" + value.toFixed(2);
    };

    var formatDescription = function (text) {
        if (!text) return "";
        var safe = String(text)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
        return safe.replace(/\n/g, "<br>");
    };

    var resolveImagePath = function (path) {
        if (!path) {
            return "";
        }
        if (/^https?:/i.test(path)) {
            return path;
        }
        var normalized = String(path).replace(/^\/+/, "");
        return "/" + normalized;
    };

    var getMinimumPrice = function (pricing) {
        if (!Array.isArray(pricing) || !pricing.length) {
            return null;
        }
        var numericValues = pricing
            .map(function (option) { return typeof option.price === "number" && !Number.isNaN(option.price) ? option.price : null; })
            .filter(function (value) { return value !== null; });
        if (!numericValues.length) {
            return null;
        }
        return Math.min.apply(Math, numericValues);
    };

    var renderServiceCards = function () {
        if (!serviceGrid) {
            return;
        }
        if (!SERVICE_CATALOG.length) {
            serviceGrid.innerHTML = "";
            if (serviceDrawerGrid) serviceDrawerGrid.innerHTML = "";
            if (servicesEmptyState) servicesEmptyState.hidden = false;
            if (servicesDrawer) servicesDrawer.setAttribute("aria-hidden", "true");
            if (serviceDrawerToggle) serviceDrawerToggle.style.display = "none";
            return;
        }

        if (servicesEmptyState) {
            servicesEmptyState.hidden = true;
        }

        var primary = SERVICE_CATALOG.slice(0, 3);
        var extra = SERVICE_CATALOG.slice(3);

        var buildCards = function (list) {
            return list.map(function (service) {
                var minPrice = service.startingPrice !== undefined ? service.startingPrice : getMinimumPrice(service.pricing);
                var priceLabel = typeof minPrice === "number" && !Number.isNaN(minPrice) ? "from " + formatPrice(minPrice) : "Custom pricing";
                var summary = service.short_description || service.description || "";
                var imageMarkup = service.image ? (
                    '<div class="card-image-container"><img src="' + resolveImagePath(service.image) + '" class="card-image" alt="' + (service.name || "Service") + ' image" loading="lazy"></div>'
                ) : "";
                return (
                    '<article class="service-card" data-animate="reveal">' +
                        imageMarkup +
                        '<div class="service-card__body">' +
                            '<h3>' + (service.name || "Service") + '</h3>' +
                            '<p class="service-card__description">' + summary + '</p>' +
                            '<button class="link-button service-read-more" type="button" data-service-id="' + service.id + '" data-auto-expand="true" style="justify-content:flex-start; padding:0;">Read More...</button>' +
                            '<p class="service-card__price">' + priceLabel + '</p>' +
                            '<button class="button button--ghost service-request-trigger" type="button" data-service-id="' + service.id + '" style="margin-top: 1.25rem; width: 100%; justify-content: center;">Request This Service</button>' +
                        '</div>' +
                    '</article>'
                );
            }).join("");
        };

        serviceGrid.innerHTML = buildCards(primary);

        if (serviceDrawerGrid) {
            serviceDrawerGrid.innerHTML = buildCards(extra);
        }

        if (serviceDrawerToggle) {
            if (extra.length) {
                serviceDrawerToggle.style.display = "inline-flex";
                var label = "View " + extra.length + " more service" + (extra.length === 1 ? "" : "s");
                serviceDrawerToggle.textContent = label;
                serviceDrawerToggle.setAttribute("data-drawer-expand-label", label);
            } else {
                serviceDrawerToggle.style.display = "none";
                if (servicesDrawer) {
                    servicesDrawer.classList.remove("is-open");
                    servicesDrawer.setAttribute("aria-hidden", "true");
                }
            }
        }
    };

    renderServiceCards();
    animatedElements = document.querySelectorAll("[data-animate]");

    if (yearSpan) {
        yearSpan.textContent = new Date().getFullYear();
    }

    if (animatedElements.length) {
        if (prefersReducedMotion) {
            animatedElements.forEach(function (element) {
                element.classList.add("is-visible");
            });
        } else {
            var isMobile = window.innerWidth <= 768;
            var revealObserver = new IntersectionObserver(function (entries, observer) {
                entries.forEach(function (entry) {
                    if (entry.isIntersecting) {
                        entry.target.classList.add("is-visible");
                        observer.unobserve(entry.target);
                    }
                });
            }, {
                threshold: isMobile ? 0 : 0.1,
                rootMargin: isMobile ? "0px" : "0px 0px -5% 0px"
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
            var isServicesToggle = item.hasAttribute("data-nav-dropdown-toggle");
            if (isServicesToggle && window.innerWidth <= 820 && servicesDropdown) {
                event.preventDefault();
                event.stopPropagation();
                var isOpen = servicesDropdown.classList.toggle("is-open");
                if (servicesDropdownToggle) {
                    servicesDropdownToggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
                }
                return;
            }

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
            // Update URL hash as user scrolls (without triggering scroll)
            if (history.replaceState && location.hash !== '#' + currentSection) {
                history.replaceState(null, '', '#' + currentSection);
            }
        }
    };

    window.addEventListener("scroll", updateActiveNav, { passive: true });
    updateActiveNav();

    // ── Share / Copy-link buttons on section headers ──
    (function initSectionShareButtons() {
        var shareSections = document.querySelectorAll('section[id]');
        var linkSvg = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>';
        var shareSvg = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>';

        shareSections.forEach(function(section) {
            var sectionId = section.id;
            if (!sectionId || sectionId.startsWith('flow-')) return; // skip modal/flow steps
            var header = section.querySelector('.section__header');
            if (!header) return;
            var title = header.querySelector('.section__title');
            if (!title) return;

            // Wrap title content for flex layout
            var wrapper = document.createElement('span');
            wrapper.className = 'section__title-wrap';
            wrapper.innerHTML = title.innerHTML;
            title.innerHTML = '';
            title.appendChild(wrapper);

            // Create share button group
            var shareGroup = document.createElement('span');
            shareGroup.className = 'section__share-group';

            // Copy link button
            var copyBtn = document.createElement('button');
            copyBtn.type = 'button';
            copyBtn.className = 'section__share-btn';
            copyBtn.setAttribute('aria-label', 'Copy link to this section');
            copyBtn.setAttribute('title', 'Copy link');
            copyBtn.innerHTML = linkSvg;
            copyBtn.addEventListener('click', function(e) {
                e.preventDefault();
                var url = location.origin + location.pathname + '#' + sectionId;
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(url).then(function() {
                        showShareToast('Link copied!');
                    });
                } else {
                    // Fallback
                    var ta = document.createElement('textarea');
                    ta.value = url;
                    ta.style.position = 'fixed';
                    ta.style.opacity = '0';
                    document.body.appendChild(ta);
                    ta.select();
                    document.execCommand('copy');
                    document.body.removeChild(ta);
                    showShareToast('Link copied!');
                }
            });
            shareGroup.appendChild(copyBtn);

            // Native share button (mobile / supported browsers)
            if (navigator.share) {
                var shareBtn = document.createElement('button');
                shareBtn.type = 'button';
                shareBtn.className = 'section__share-btn';
                shareBtn.setAttribute('aria-label', 'Share this section');
                shareBtn.setAttribute('title', 'Share');
                shareBtn.innerHTML = shareSvg;
                shareBtn.addEventListener('click', function(e) {
                    e.preventDefault();
                    navigator.share({
                        title: document.title,
                        url: location.origin + location.pathname + '#' + sectionId
                    }).catch(function() {});
                });
                shareGroup.appendChild(shareBtn);
            }

            title.appendChild(shareGroup);
        });

        // Toast notification
        function showShareToast(msg) {
            var existing = document.querySelector('.share-toast');
            if (existing) existing.remove();
            var toast = document.createElement('div');
            toast.className = 'share-toast';
            toast.textContent = msg;
            document.body.appendChild(toast);
            requestAnimationFrame(function() {
                toast.classList.add('is-visible');
            });
            setTimeout(function() {
                toast.classList.remove('is-visible');
                setTimeout(function() { toast.remove(); }, 300);
            }, 2000);
        }
    })();

    // ── On page load: scroll to hash section ──
    if (location.hash) {
        var hashTarget = document.querySelector(location.hash);
        if (hashTarget) {
            setTimeout(function() {
                hashTarget.scrollIntoView({ behavior: 'smooth' });
            }, 400);
        }
    }

    // Mobile nav toggle
    if (navToggle && nav) {
        navToggle.addEventListener("click", function () {
            var isOpen = nav.classList.toggle("nav--open");
            navToggle.setAttribute("aria-expanded", String(isOpen));
            if (!isOpen && servicesDropdown) {
                servicesDropdown.classList.remove("is-open");
                if (servicesDropdownToggle) servicesDropdownToggle.setAttribute("aria-expanded", "false");
            }
        });

        // Close menu when a link is clicked
        if (primaryNav) {
            primaryNav.addEventListener("click", function (e) {
                var target = e.target;
                var clickedDropdownToggle = target && target.closest && target.closest("[data-nav-dropdown-toggle]");
                if (clickedDropdownToggle) {
                    return;
                }
                if (target && target.closest && (target.closest(".nav__item") || target.closest(".nav__dropdown-link"))) {
                    nav.classList.remove("nav--open");
                    navToggle.setAttribute("aria-expanded", "false");
                    if (servicesDropdown) {
                        servicesDropdown.classList.remove("is-open");
                    }
                    if (servicesDropdownToggle) {
                        servicesDropdownToggle.setAttribute("aria-expanded", "false");
                    }
                }
            });
        }

        document.addEventListener("click", function (e) {
            if (!servicesDropdown || !servicesDropdownToggle) {
                return;
            }
            if (window.innerWidth > 820) {
                return;
            }
            if (!servicesDropdown.contains(e.target)) {
                servicesDropdown.classList.remove("is-open");
                servicesDropdownToggle.setAttribute("aria-expanded", "false");
            }
        });

        // Close on Escape
        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape") {
                nav.classList.remove("nav--open");
                if (navToggle) navToggle.setAttribute("aria-expanded", "false");
                if (servicesDropdown) servicesDropdown.classList.remove("is-open");
                if (servicesDropdownToggle) servicesDropdownToggle.setAttribute("aria-expanded", "false");
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
        var _preloaderDone = false;
        var finalizePreloader = function () {
            if (_preloaderDone) return;
            _preloaderDone = true;
            body.classList.add("is-loaded");
            preloader.classList.add("is-hidden");
            // Remove after transition (fallback after 800ms if transitionend never fires)
            window.setTimeout(function () {
                if (preloader.parentNode) preloader.remove();
            }, 800);
        };

        // Skip preloader entirely when navigating back with a booking intent:
        // ?service_id=, ?contract_frequency=, or any #services* hash
        var _qs = window.location.search || "";
        var _hash = window.location.hash || "";
        var _isBookingReturn = _qs.indexOf("service_id=") !== -1 ||
                               _qs.indexOf("contract_frequency=") !== -1 ||
                               _qs.indexOf("service_day=") !== -1 ||
                               _hash.indexOf("#services") === 0 ||
                               _hash.indexOf("#service-") === 0 ||
                               _hash === "#book";

        if (_isBookingReturn || prefersReducedMotion) {
            // Hide immediately — no animation
            if (preloader.parentNode) preloader.remove();
            body.classList.add("is-loaded");
            _preloaderDone = true;
        } else {
            // Hide preloader as soon as DOM is interactive — don't wait for images/fonts
            if (document.readyState === "loading") {
                document.addEventListener("DOMContentLoaded", function () {
                    window.setTimeout(finalizePreloader, 100);
                });
            } else {
                window.setTimeout(finalizePreloader, 100);
            }

            // Hard cap: never show more than 1.25 seconds regardless
            window.setTimeout(finalizePreloader, 1250);

            window.addEventListener("load", function () {
                finalizePreloader();
            });
        }
    } else {
        body.classList.add("is-loaded");
    }

    var apiBase = window.location.origin;

    var sendAnalyticsEvent = function (eventType, eventData) {
        if (!eventType) {
            return;
        }
        try {
            fetch(apiBase + "/api/analytics/event", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    event_type: eventType,
                    event_data: eventData || {}
                })
            }).catch(function () {
                /* Best-effort logging */
            });
        } catch (error) {
            /* Ignore analytics failures */
        }
    };

    // Testimonial Form
    var testimonialForm = document.getElementById("testimonial-form");
    var testimonialFeedback = document.getElementById("testimonial-feedback");
    var starRating = document.getElementById("star-rating");
    var ratingInput = document.getElementById("testimonial-rating");

    // Star rating interaction
    if (starRating && ratingInput) {
        var starButtons = starRating.querySelectorAll(".star-btn");
        
        starButtons.forEach(function (btn) {
            btn.addEventListener("click", function () {
                var rating = parseInt(btn.getAttribute("data-rating"), 10);
                ratingInput.value = rating;
                
                // Update visual state
                starButtons.forEach(function (star) {
                    var starValue = parseInt(star.getAttribute("data-rating"), 10);
                    if (starValue <= rating) {
                        star.classList.add("active");
                    } else {
                        star.classList.remove("active");
                    }
                });
            });
            
            // Hover effect
            btn.addEventListener("mouseenter", function () {
                var hoverRating = parseInt(btn.getAttribute("data-rating"), 10);
                starButtons.forEach(function (star) {
                    var starValue = parseInt(star.getAttribute("data-rating"), 10);
                    if (starValue <= hoverRating) {
                        star.style.color = "#f59e0b";
                    } else {
                        star.style.color = "";
                    }
                });
            });
        });
        
        // Reset on mouse leave
        starRating.addEventListener("mouseleave", function () {
            var currentRating = parseInt(ratingInput.value, 10);
            starButtons.forEach(function (star) {
                star.style.color = "";
                var starValue = parseInt(star.getAttribute("data-rating"), 10);
                if (starValue <= currentRating) {
                    star.classList.add("active");
                } else {
                    star.classList.remove("active");
                }
            });
        });
    }

    // Testimonial form submission
    if (testimonialForm && testimonialFeedback) {
        var testimonialSubmitBtn = testimonialForm.querySelector('button[type="submit"]');
        var testimonialSubmitting = false;

        testimonialForm.addEventListener("submit", async function (event) {
            event.preventDefault();

            if (testimonialSubmitting) return;
            testimonialSubmitting = true;

            if (testimonialSubmitBtn) {
                testimonialSubmitBtn.disabled = true;
                testimonialSubmitBtn.textContent = "Submitting...";
            }

            var name = (testimonialForm.elements.name.value || "").trim();
            var email = (testimonialForm.elements.email.value || "").trim();
            var rating = parseInt(ratingInput ? ratingInput.value : 5, 10);
            var message = (testimonialForm.elements.message.value || "").trim();

            testimonialFeedback.classList.remove("is-error", "is-success");
            testimonialFeedback.textContent = "";

            // Validate
            if (!name) {
                testimonialFeedback.textContent = "Please enter your name.";
                testimonialFeedback.classList.add("is-error");
                testimonialSubmitting = false;
                if (testimonialSubmitBtn) {
                    testimonialSubmitBtn.disabled = false;
                    testimonialSubmitBtn.textContent = "Submit Review";
                }
                return;
            }

            if (!message || message.length < 10) {
                testimonialFeedback.textContent = "Please write a review (at least 10 characters).";
                testimonialFeedback.classList.add("is-error");
                testimonialSubmitting = false;
                if (testimonialSubmitBtn) {
                    testimonialSubmitBtn.disabled = false;
                    testimonialSubmitBtn.textContent = "Submit Review";
                }
                return;
            }

            try {
                var response = await fetch(apiBase + "/api/testimonials/submit", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        name: name,
                        email: email,
                        rating: rating,
                        message: message
                    })
                });

                var data = await response.json();

                if (response.ok) {
                    testimonialFeedback.textContent = data.message || "Thank you for your review!";
                    testimonialFeedback.classList.add("is-success");
                    testimonialForm.reset();
                    // Reset stars to 5
                    if (ratingInput) ratingInput.value = 5;
                    if (starRating) {
                        starRating.querySelectorAll(".star-btn").forEach(function (star) {
                            star.classList.add("active");
                        });
                    }
                    sendAnalyticsEvent("testimonial_submission", { verified: data.is_verified });
                } else {
                    testimonialFeedback.textContent = data.error || "Unable to submit review. Please try again.";
                    testimonialFeedback.classList.add("is-error");
                }
            } catch (error) {
                testimonialFeedback.textContent = "Unable to submit review. Please try again.";
                testimonialFeedback.classList.add("is-error");
                console.error("Testimonial submission failed", error);
            } finally {
                testimonialSubmitting = false;
                if (testimonialSubmitBtn) {
                    testimonialSubmitBtn.disabled = false;
                    testimonialSubmitBtn.textContent = "Submit Review";
                }
            }
        });
    }

    // Multi-step Service Flow
    var serviceModal = document.getElementById("service-modal");
    var postcodeModal = document.getElementById("postcode-modal");
    var coverageModal = document.getElementById("coverage-modal");
    var extendedCoverageModal = document.getElementById("extended-coverage-modal");
    var extendedCoverageFee = document.getElementById("extended-coverage-fee");
    var extendedCoverageMessage = document.getElementById("extended-coverage-message");
    var extendedCoverageProceed = document.getElementById("extended-coverage-proceed");
    var extendedCoverageCancel = document.getElementById("extended-coverage-cancel");
    var extendedCoverageClose = document.getElementById("extended-coverage-close");
    var pendingExtendedCoverageQuote = null;
    var postcodeForm = document.getElementById("postcode-form");
    var postcodeInput = document.getElementById("postcode-entry");
    var postcodeFeedback = document.getElementById("postcode-feedback");
    var postcodeSubmitButton = postcodeForm ? postcodeForm.querySelector("[data-postcode-submit]") : null;
    var editLocationButton = document.getElementById("flow-edit-location");
    var serviceFlowForm = document.getElementById("service-request-flow");
    var pendingServiceId = null;
    var pendingAutoExpand = false;
    var autoExpandDescription = false;
    var flowReadOnlyMode = false;

    if (serviceModal && serviceFlowForm) {
        var flowSteps = Array.from(serviceModal.querySelectorAll("[data-flow-step]"));
        var flowIndicators = Array.from(serviceModal.querySelectorAll("[data-flow-step-indicator]"));
        var flowOptionsContainer = document.getElementById("flow-options");
        var flowServiceName = document.getElementById("flow-service-name");
        var flowServiceDescription = document.getElementById("flow-service-description");
        var flowServiceIndex = document.getElementById("flow-service-index");
        var flowServiceCount = document.getElementById("flow-service-count");
        var flowPriceDisplay = document.getElementById("flow-price-display");
        var flowNextButton = serviceModal.querySelector("[data-flow-next]");
        var flowSurveyButton = null;
        var flowPrevButton = serviceModal.querySelector("[data-flow-prev]");
        var flowSkipButton = serviceModal.querySelector("[data-flow-skip]");
        var flowChoicePrompt = document.getElementById("flow-choice-prompt");
        var flowChoiceSelected = document.getElementById("flow-choice-selected");
        var flowChoiceQuestion = document.getElementById("flow-choice-question");
        var flowChoiceBrowse = document.getElementById("flow-choice-browse");
        var flowChoiceContinue = document.getElementById("flow-choice-continue");
        var flowSummaryBackButton = serviceModal.querySelector("[data-flow-summary-back]");
        var flowSummaryNextButton = serviceModal.querySelector("[data-flow-summary-next]");
        var flowScheduleBackButton = serviceModal.querySelector("[data-flow-schedule-back]");
        var flowSubmitButton = serviceModal.querySelector("[data-flow-submit]");
        var flowMiniCartList = document.getElementById("flow-mini-cart");
        var flowMiniCartTotal = document.getElementById("flow-mini-cart-total");
        var flowMiniServicesTotal = document.getElementById("flow-mini-services-total");
        var flowMiniLogisticsRow = document.getElementById("flow-mini-logistics-row");
        var flowMiniLogisticsFee = document.getElementById("flow-mini-logistics-fee");
        var flowMiniCount = document.getElementById("flow-mini-count");
        var flowSummaryList = document.getElementById("flow-summary-services");
        var flowSummaryServicesTotal = document.getElementById("flow-summary-services-total");
        var flowSummaryTravelRow = document.getElementById("flow-summary-travel-row");
        var flowSummaryTravel = document.getElementById("flow-summary-travel");
        var flowSummaryTravelNote = document.getElementById("flow-summary-travel-note");
        var flowSummaryTotal = document.getElementById("flow-summary-total");
        var flowSummaryFeedback = document.getElementById("flow-summary-feedback");
        var flowSummaryPostcodeInput = document.getElementById("flow-summary-postcode");
        var flowRefreshTravelButton = document.querySelector("[data-flow-refresh-travel]");
        var flowFeedback = document.getElementById("flow-feedback");
        var flowNameInput = document.getElementById("flow-name");
        var flowEmailInput = document.getElementById("flow-email");
        var flowPhoneInput = document.getElementById("flow-phone");
        var flowLocationInput = document.getElementById("flow-location");
        var flowPostcodeInput = document.getElementById("flow-postcode");
        var flowDateInput = document.getElementById("flow-date");
        var flowTimeInput = document.getElementById("flow-time");
        var flowContractFrequencyRow = document.getElementById("flow-contract-frequency-row");
        var flowContractFrequencyInput = document.getElementById("flow-contract-frequency");
        var flowContractSignerRow = document.getElementById("flow-contract-signer-row");
        var flowContractSignerInput = document.getElementById("flow-contract-signer");
        var flowContractTermsRow = document.getElementById("flow-contract-terms-row");
        var flowContractTermsInput = document.getElementById("flow-contract-terms");
        var flowNotesInput = document.getElementById("flow-notes");
        var flowPaymentOptionInputs = serviceFlowForm ? serviceFlowForm.querySelectorAll('input[name="flow_payment_option"]') : [];
        var FLOW_STATE_KEY = "serviceFlowWizard";
        var currentServiceIndex = 0;
        var activeStep = 1;
        var serviceQueue = []; // Reordered service IDs for the flow
        var submissionPending = false; // Lock to prevent duplicate form submissions
        var pendingDomesticEntry = false; // True when the flow was triggered from a domestic CTA
        var defaultSubmitLabel = flowSubmitButton ? flowSubmitButton.textContent : "Submit Request";
        var surveySubmitLabel = "Submit Enquiry";

        var setSubmitButtonLabel = function (hasSurvey) {
            if (!flowSubmitButton) {
                return;
            }
            // Hide prebook (Pay Online) option for survey/custom services — no payment taken
            var prebookLabel = serviceFlowForm ? serviceFlowForm.querySelector('.flow-payment-choice__option input[value="prebook_save"]') : null;
            var prebookWrap = prebookLabel ? prebookLabel.closest('.flow-payment-choice__option') : null;
            if (prebookWrap) {
                prebookWrap.style.display = hasSurvey ? 'none' : '';
                // If prebook was selected and we're switching to survey, reset to pay_in_person
                if (hasSurvey && normalizePaymentOptionValue(flowState && flowState.payment_option) === 'prebook_save') {
                    flowState.payment_option = 'pay_in_person';
                    var inPersonInput = serviceFlowForm ? serviceFlowForm.querySelector('input[name="flow_payment_option"][value="pay_in_person"]') : null;
                    if (inPersonInput) inPersonInput.checked = true;
                }
            }
            if (hasSurvey) {
                flowSubmitButton.textContent = surveySubmitLabel;
                return;
            }
            var selectedPaymentOption = normalizePaymentOptionValue(flowState && flowState.payment_option);
            flowSubmitButton.textContent = selectedPaymentOption === "prebook_save" ? "Proceed to Secure Payment" : defaultSubmitLabel;
        };

        var createDefaultFlowState = function () {
            return {
                selections: {},
                customer: {
                    name: "",
                    email: "",
                    phone: "",
                    location: "",
                    postcode: ""
                },
                schedule: {
                    preferred_date: "",
                    preferred_time: "",
                    contract_frequency: ""
                },
                contract: {
                    signer_name: "",
                    service_day: "",
                    agreed: false
                },
                notes: "",
                payment_option: "pay_in_person",
                travelQuote: null,
                travelPostcodeSnapshot: "",
                extendedCoverageAccepted: false,
                domesticPlan: null,
                domesticConfig: null   // { plan_id, plan_name, price_per_hour, cleaners, hours, total }
            };
        };

        var loadFlowState = function () {
            try {
                var stored = sessionStorage.getItem(FLOW_STATE_KEY);
                if (stored) {
                    return Object.assign(createDefaultFlowState(), JSON.parse(stored));
                }
            } catch (error) {
                console.warn("Unable to load service flow state", error);
            }
            return createDefaultFlowState();
        };

        var flowState = loadFlowState();
        if (!flowState.schedule || typeof flowState.schedule !== "object") {
            flowState.schedule = { preferred_date: "", preferred_time: "", contract_frequency: "" };
        }
        if (flowState.schedule.contract_frequency === undefined || flowState.schedule.contract_frequency === null) {
            flowState.schedule.contract_frequency = "";
        }
        if (!flowState.contract || typeof flowState.contract !== "object") {
            flowState.contract = { signer_name: "", service_day: "", agreed: false };
        }
        if (flowState.contract.agreed === undefined || flowState.contract.agreed === null) {
            flowState.contract.agreed = false;
        }
        if (flowState.contract.service_day === undefined || flowState.contract.service_day === null) {
            flowState.contract.service_day = "";
        }
        if (flowState.travelPostcodeSnapshot === undefined) {
            flowState.travelPostcodeSnapshot = "";
        }
        flowState.payment_option = normalizePaymentOptionValue(flowState.payment_option);

        var syncPaymentOptionInputs = function () {
            if (!flowPaymentOptionInputs || !flowPaymentOptionInputs.length) {
                return;
            }
            Array.prototype.forEach.call(flowPaymentOptionInputs, function (input) {
                input.checked = normalizePaymentOptionValue(input.value) === flowState.payment_option;
            });
        };

        if (flowPaymentOptionInputs && flowPaymentOptionInputs.length) {
            Array.prototype.forEach.call(flowPaymentOptionInputs, function (input) {
                input.addEventListener("change", function () {
                    flowState.payment_option = normalizePaymentOptionValue(input.value);
                    persistFlowState();
                    var hasSurvey = Boolean(lastSummaryTotals && lastSummaryTotals.hasSurvey);
                    setSubmitButtonLabel(hasSurvey);
                });
            });
        }
        syncPaymentOptionInputs();
        setSubmitButtonLabel(Boolean(lastSummaryTotals && lastSummaryTotals.hasSurvey));

        var updateTravelSnapshot = function (value) {
            flowState.travelPostcodeSnapshot = normalizePostcodeValue(value || "");
        };
        var storedPostcode = getStoredPostcode();
        var storedQuote = getStoredTravelQuote();
        if (askForPostcode) {
            if (storedPostcode && !flowState.customer.postcode) {
                flowState.customer.postcode = storedPostcode;
            }
            if (storedQuote && !flowState.travelQuote) {
                flowState.travelQuote = storedQuote;
            }
            if (!flowState.travelPostcodeSnapshot && (flowState.customer.postcode || storedPostcode)) {
                updateTravelSnapshot(flowState.customer.postcode || storedPostcode);
            }
        }

        var persistFlowState = function () {
            try {
                sessionStorage.setItem(FLOW_STATE_KEY, JSON.stringify(flowState));
            } catch (error) {
                console.warn("Unable to persist service flow state", error);
            }
        };

        var shouldRefreshTravelQuote = function (value) {
            if (!askForPostcode) {
                return false;
            }
            var target = normalizePostcodeValue(value || flowState.customer.postcode || "");
            var snapshot = normalizePostcodeValue(flowState.travelPostcodeSnapshot || "");
            if (!target) {
                return Boolean(snapshot);
            }
            if (!flowState.travelQuote) {
                return true;
            }
            return snapshot !== target;
        };

        var getOrderedSelections = function () {
            var results = [];

            // Include configured domestic plan as the first item
            if (flowState.domesticConfig) {
                var dc = flowState.domesticConfig;
                var total = dc.total || 0;
                results.push({
                    serviceId: "domestic_" + dc.plan_id,
                    serviceName: "Domestic Cleaning",
                    optionLabel: dc.plan_name,
                    optionDetails: dc.cleaners + " cleaner" + (dc.cleaners > 1 ? "s" : "") + " \xD7 " + dc.hours + " hrs @ " + formatPrice(parseFloat(dc.price_per_hour)) + "/hr",
                    price: total,
                    priceDisplay: formatPrice(total),
                    modelType: "domestic",
                    payload: {
                        type: "domestic",
                        plan_id: dc.plan_id,
                        plan_name: dc.plan_name,
                        price_per_hour: dc.price_per_hour,
                        cleaners: dc.cleaners,
                        hours: dc.hours,
                        total: dc.total
                    },
                    isDomestic: true
                });
            }

            SERVICE_CATALOG.forEach(function (service) {
                var selection = flowState.selections[service.id];
                if (!selection) {
                    return;
                }
                var normalizedPrice = normalizePriceValue(selection.price);
                var optionLabel = selection.optionLabel || selection.label || "Custom package";
                var optionDetails = selection.optionDetails || selection.details || "";
                results.push(Object.assign({
                    serviceId: service.id,
                    serviceName: service.name,
                    serviceCategory: selection.serviceCategory || service.service_category || "one_time",
                    optionLabel: optionLabel,
                    optionDetails: optionDetails,
                    price: normalizedPrice,
                    priceDisplay: selection.priceDisplay || (normalizedPrice !== null ? formatPrice(normalizedPrice) : "Custom quote"),
                    modelType: selection.modelType || null,
                    payload: selection.payload || null
                }, selection));
            });

            return results;
        };

        var hasContractSelections = function (selectionList) {
            var list = Array.isArray(selectionList) ? selectionList : getOrderedSelections();
            return list.some(function (selection) {
                if (selection && selection.isDomestic) {
                    return false;
                }
                return String((selection && selection.serviceCategory) || "one_time").toLowerCase() === "contract";
            });
        };

        var hasHybridSelections = function (selectionList) {
            var list = Array.isArray(selectionList) ? selectionList : getOrderedSelections();
            return list.some(function (selection) {
                return String((selection && selection.serviceCategory) || "one_time").toLowerCase() === "hybrid";
            });
        };

        var updateContractFrequencyVisibility = function () {
            if (!flowContractFrequencyRow) {
                return;
            }
            var isContract = hasContractSelections();
            var isHybrid = hasHybridSelections();
            var shouldShow = isContract || isHybrid;
            var frequencyRequired = isContract && !isHybrid; // hybrid: optional; pure contract: required

            flowContractFrequencyRow.hidden = !shouldShow;

            // For hybrid, show a hint that frequency is optional
            var freqHint = flowContractFrequencyRow.querySelector('.form-hint');
            if (freqHint) {
                freqHint.textContent = isHybrid && !isContract
                    ? "Optional — choose a frequency to set up a recurring contract, or leave blank for a one-time booking."
                    : "Only required for contract-based services.";
            }

            // Signer name and terms checkbox are handled by the contract modal — keep hidden
            if (flowContractSignerRow) {
                flowContractSignerRow.hidden = true;
            }
            if (flowContractTermsRow) {
                flowContractTermsRow.hidden = true;
            }
            if (flowContractFrequencyInput) {
                flowContractFrequencyInput.required = frequencyRequired;
                if (!shouldShow) {
                    flowContractFrequencyInput.value = "";
                    flowState.schedule.contract_frequency = "";
                    if (flowContractSignerInput) flowContractSignerInput.value = "";
                    if (flowContractTermsInput) flowContractTermsInput.checked = false;
                    flowState.contract.signer_name = "";
                    flowState.contract.agreed = false;
                    persistFlowState();
                }
            }
            if (flowContractSignerInput) {
                // Signer only required if they actually chose a frequency (for hybrid) or it's contract
                flowContractSignerInput.required = frequencyRequired || (isHybrid && !!flowContractFrequencyInput && !!flowContractFrequencyInput.value);
            }
        };

        var lastSummaryTotals = { serviceTotal: 0, hasCustom: false, hasSurvey: false };

        var updateMiniCartTotals = function (serviceTotal, travelFee, hasCustom, hasSurvey) {
            if (!flowMiniCartTotal) {
                return;
            }
            var visibleTravel = typeof travelFee === "number" && travelFee > 0 && !hasSurvey;
            if (flowMiniServicesTotal) {
                if (hasSurvey) {
                    flowMiniServicesTotal.textContent = "To be confirmed";
                } else {
                    flowMiniServicesTotal.textContent = hasCustom && !serviceTotal ? "Custom quote" : hasCustom ? "from " + formatPrice(serviceTotal || 0) : formatPrice(serviceTotal || 0);
                }
            }
            if (flowMiniLogisticsRow) {
                flowMiniLogisticsRow.style.display = visibleTravel ? "block" : "none";
            }
            if (flowMiniLogisticsFee) {
                flowMiniLogisticsFee.textContent = visibleTravel ? formatPrice(travelFee) : "—";
            }

            var grand = null;
            if (!hasSurvey && typeof serviceTotal === "number" && !Number.isNaN(serviceTotal)) {
                grand = serviceTotal;
            }
            if (visibleTravel) {
                grand = (grand || 0) + travelFee;
            }

            if (flowMiniCartTotal) {
                if (hasSurvey) {
                    flowMiniCartTotal.textContent = "To be confirmed";
                } else if (hasCustom && grand === null) {
                    flowMiniCartTotal.textContent = "Custom quote";
                } else if (hasCustom && grand !== null) {
                    flowMiniCartTotal.textContent = "from " + formatPrice(grand);
                } else {
                    flowMiniCartTotal.textContent = grand === null ? "—" : formatPrice(grand);
                }
            }
        };

        var updateSummaryTotals = function (serviceTotal, hasCustom, travelMessage, hasSurvey) {
            lastSummaryTotals = { serviceTotal: serviceTotal, hasCustom: hasCustom, hasSurvey: hasSurvey };

            if (flowSummaryServicesTotal) {
                if (hasSurvey) {
                    flowSummaryServicesTotal.textContent = "To be confirmed (survey required)";
                } else {
                    flowSummaryServicesTotal.textContent = hasCustom && !serviceTotal ? "Custom quote" : hasCustom ? "from " + formatPrice(serviceTotal) : formatPrice(serviceTotal);
                }
            }

            var travelQuote = flowState.travelQuote || getStoredTravelQuote();
            var travelFee = travelQuote && typeof travelQuote.travel_fee === "number" && !Number.isNaN(travelQuote.travel_fee) ? travelQuote.travel_fee : null;
            var travelVisible = typeof travelFee === "number" && travelFee > 0;
            var travelNote = "";
            var isExtendedCoverage = travelQuote && travelQuote.is_extended_coverage === true;

            if (hasSurvey) {
                travelVisible = false;
                travelFee = null;
            }

            if (travelVisible) {
                var baseNote = "Includes call-out, equipment transport, and distance coverage.";
                if (isExtendedCoverage) {
                    baseNote = "Extended coverage area. " + baseNote;
                }
                if (travelMessage) {
                    travelNote = baseNote + " " + travelMessage;
                } else if (travelQuote && travelQuote.pricing_method === "tomtom") {
                    travelNote = baseNote + " Based on live traffic data.";
                } else {
                    travelNote = baseNote;
                }
            }

            if (flowSummaryTravelRow) {
                flowSummaryTravelRow.style.display = travelVisible ? "flex" : "none";
            }
            if (flowSummaryTravel) {
                flowSummaryTravel.textContent = travelVisible ? formatPrice(travelFee) : "—";
            }
            if (flowSummaryTravelNote) {
                flowSummaryTravelNote.textContent = travelVisible ? (travelNote || "") : "";
            }

            var grandTotal = null;
            if (typeof serviceTotal === "number" && !Number.isNaN(serviceTotal)) {
                grandTotal = serviceTotal;
            }
            if (typeof travelFee === "number" && !Number.isNaN(travelFee)) {
                grandTotal = (grandTotal || 0) + travelFee;
            }

            if (flowSummaryTotal) {
                if (hasSurvey) {
                    flowSummaryTotal.textContent = "To be confirmed (survey required)";
                } else if (hasCustom && grandTotal === null) {
                    flowSummaryTotal.textContent = "Custom quote";
                } else if (hasCustom && grandTotal !== null) {
                    flowSummaryTotal.textContent = "from " + formatPrice(grandTotal);
                } else {
                    flowSummaryTotal.textContent = grandTotal === null ? "—" : formatPrice(grandTotal);
                }
            }

            updateMiniCartTotals(serviceTotal, travelVisible ? travelFee : null, hasCustom, hasSurvey);
            if (askForPostcode) {
                updateSummaryNextState();
            }
        };

        var fetchTravelQuote = async function (postcodeValue, baseAmount, skipExtendedCheck) {
            var trimmedPostcode = (postcodeValue || "").trim();
            var normalizedPostcode = normalizePostcodeValue(trimmedPostcode);
            if (!normalizedPostcode) {
                setStoredTravelQuote(null);
                flowState.travelQuote = null;
                updateTravelSnapshot("");
                return null;
            }

            var response = await fetch(apiBase + "/api/travel-quote", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({ postcode: trimmedPostcode, base_amount: baseAmount || 0 })
            });
            var data = await response.json().catch(function () { return {}; });

            if (response.ok) {
                // Check if this is extended coverage and user hasn't accepted it yet
                if (data.is_extended_coverage && !skipExtendedCheck && !flowState.extendedCoverageAccepted) {
                    // Store the postcode in the data for later use
                    data.customer_postcode = trimmedPostcode || normalizedPostcode;
                    // Return special object to indicate extended coverage needs confirmation
                    return { requiresExtendedConfirmation: true, quote: data };
                }
                
                flowState.travelQuote = data;
                flowState.customer.postcode = trimmedPostcode;
                updateTravelSnapshot(trimmedPostcode);
                persistFlowState();
                setStoredTravelQuote(data, trimmedPostcode);
                return data;
            }

            setStoredTravelQuote(null);
            flowState.travelQuote = null;
            updateTravelSnapshot("");
            if (data && data.code === "out_of_area") {
                openCoverageModal();
            }
            throw new Error(data.error || "Unable to calculate travel.");
        };

        // Lock to prevent concurrent travel quote requests
        var travelQuotePending = false;
        var lastTravelQuotePostcode = "";

        var summaryTravelNeedsRefresh = false;
        var summaryBlockMessageOverride = "";
        var summaryInstructionLocked = false;

        var setSummaryFeedbackMessage = function (text, tone, isInstruction) {
            if (!flowSummaryFeedback) {
                summaryInstructionLocked = false;
                return;
            }
            flowSummaryFeedback.textContent = text || "";
            flowSummaryFeedback.classList.remove("is-error", "is-success");
            if (tone === "error") {
                flowSummaryFeedback.classList.add("is-error");
            } else if (tone === "success") {
                flowSummaryFeedback.classList.add("is-success");
            }
            summaryInstructionLocked = Boolean(isInstruction && text);
            if (!text) {
                summaryInstructionLocked = false;
            }
        };

        var updateSummaryNextState = function () {
            if (!flowSummaryNextButton) {
                return;
            }

            if (!askForPostcode || lastSummaryTotals.hasSurvey) {
                flowSummaryNextButton.disabled = false;
                if (summaryInstructionLocked) {
                    setSummaryFeedbackMessage("", null, false);
                }
                summaryBlockMessageOverride = "";
                return;
            }

            var normalizedPostcode = normalizePostcodeValue(flowState.customer.postcode || "");
            var disable = false;
            var message = summaryBlockMessageOverride || "";

            if (!normalizedPostcode) {
                disable = true;
                if (!message) {
                    message = "Add your postcode or address to continue.";
                }
            } else if (summaryTravelNeedsRefresh) {
                disable = true;
                if (!message) {
                    message = "Click Update travel to refresh pricing.";
                }
            } else if (travelQuotePending) {
                disable = true;
                if (!message) {
                    message = "Calculating travel...";
                }
            }

            flowSummaryNextButton.disabled = disable;

            if (disable) {
                setSummaryFeedbackMessage(message, "error", true);
            } else if (summaryInstructionLocked) {
                setSummaryFeedbackMessage("", null, false);
            }
        };

        var refreshTravelQuote = async function (baseAmount, hasCustom, skipExtendedCheck) {
            if (!askForPostcode) {
                return;
            }

            var postcodeValue = (flowSummaryPostcodeInput && flowSummaryPostcodeInput.value) || (flowPostcodeInput && flowPostcodeInput.value) || flowState.customer.postcode || "";
            postcodeValue = postcodeValue.trim();
            var normalizedPostcode = normalizePostcodeValue(postcodeValue);

            // Prevent duplicate concurrent requests for same postcode
            if (travelQuotePending && normalizedPostcode && normalizedPostcode === lastTravelQuotePostcode) {
                return;
            }

            flowState.customer.postcode = postcodeValue;
            persistFlowState();
            summaryBlockMessageOverride = "";
            setSummaryFeedbackMessage("", null, false);

            if (!normalizedPostcode) {
                flowState.travelQuote = null;
                flowState.extendedCoverageAccepted = false;
                setStoredTravelQuote(null);
                updateTravelSnapshot("");
                summaryTravelNeedsRefresh = false;
                lastTravelQuotePostcode = "";
                updateSummaryNextState();
                updateSummaryTotals(baseAmount, hasCustom, "", lastSummaryTotals.hasSurvey || false);
                return;
            }

            // Set lock
            travelQuotePending = true;
            lastTravelQuotePostcode = normalizedPostcode;
            updateSummaryNextState();

            try {
                var result = await fetchTravelQuote(postcodeValue, baseAmount || 0, skipExtendedCheck || flowState.extendedCoverageAccepted);
                
                // Check if extended coverage confirmation is needed
                if (result && result.requiresExtendedConfirmation) {
                    travelQuotePending = false;
                    summaryTravelNeedsRefresh = true;
                    summaryBlockMessageOverride = "Confirm extended coverage to continue.";
                    updateSummaryNextState();
                    openExtendedCoverageModal(result.quote);
                    return;
                }

                var quote = result;

                // Only show "Travel estimate updated" if there's actually a fee > 0
                summaryTravelNeedsRefresh = false;
                summaryBlockMessageOverride = "";
                var hasFee = quote && typeof quote.travel_fee === "number" && quote.travel_fee > 0;
                var isExtended = quote && quote.is_extended_coverage === true;
                if (hasFee) {
                    setSummaryFeedbackMessage(isExtended ? "Extended coverage travel estimate updated." : "Travel estimate updated.", "success", false);
                } else {
                    setSummaryFeedbackMessage("", null, false);
                }
                updateSummaryTotals(baseAmount, hasCustom, "", lastSummaryTotals.hasSurvey || false);
            } catch (error) {
                summaryBlockMessageOverride = error.message || "Unable to calculate travel.";
                summaryTravelNeedsRefresh = true;
                updateSummaryTotals(baseAmount, hasCustom, error.message || "Unable to calculate travel.", lastSummaryTotals.hasSurvey || false);
            } finally {
                travelQuotePending = false;
                updateSummaryNextState();
            }
        };

        var requestTravelQuoteIfChanged = function (value) {
            if (!askForPostcode) {
                return;
            }
            var candidate = value !== undefined ? value : (flowState.customer.postcode || "");
            if (!shouldRefreshTravelQuote(candidate)) {
                return;
            }
            refreshTravelQuote(lastSummaryTotals.serviceTotal, lastSummaryTotals.hasCustom);
        };

        var updateMiniCart = function () {
            if (!flowMiniCartList || !flowMiniCartTotal || !flowMiniCount) {
                return;
            }
            var selections = getOrderedSelections();
            flowMiniCartList.innerHTML = "";
            if (!selections.length) {
                var placeholder = document.createElement("li");
                placeholder.textContent = "No services selected yet.";
                flowMiniCartList.appendChild(placeholder);
            } else {
                selections.forEach(function (selection) {
                    var item = document.createElement("li");
                    item.className = "flow-mini-cart__item";
                    var detail = selection.optionDetails ? " • " + selection.optionDetails : "";
                    var textSpan = document.createElement("span");
                    textSpan.className = "flow-mini-cart__item-text";
                    textSpan.textContent = selection.serviceName + " – " + selection.optionLabel + detail + " (" + (selection.priceDisplay || formatPrice(selection.price)) + ")";
                    item.appendChild(textSpan);
                    
                    // Add remove button
                    var removeBtn = document.createElement("button");
                    removeBtn.type = "button";
                    removeBtn.className = "flow-mini-cart__remove";
                    removeBtn.setAttribute("aria-label", "Remove " + selection.serviceName);
                    removeBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>';
                    removeBtn.addEventListener("click", (function (capturedServiceId) {
                        return function (e) {
                            e.stopPropagation();
                            delete flowState.selections[capturedServiceId];
                            persistFlowState();
                            updateMiniCart();
                            updateContractFrequencyVisibility();
                            if (activeStep === 2) {
                                renderSummary();
                            } else {
                                // Navigate to the removed service so the user sees it cleared
                                var removedIndex = serviceQueue.indexOf(String(capturedServiceId));
                                if (removedIndex < 0) {
                                    removedIndex = serviceQueue.indexOf(capturedServiceId);
                                }
                                if (removedIndex >= 0 && removedIndex < serviceQueue.length) {
                                    currentServiceIndex = removedIndex;
                                }
                                renderServiceStep();
                            }
                        };
                    })(selection.serviceId));
                    item.appendChild(removeBtn);
                    
                    flowMiniCartList.appendChild(item);
                });
            }
            flowMiniCount.textContent = selections.length;
            var serviceTotal = selections.reduce(function (sum, selection) {
                if (typeof selection.price === "number" && !Number.isNaN(selection.price)) {
                    return sum + selection.price;
                }
                return sum;
            }, 0);
            var hasCustom = selections.some(function (selection) { return typeof selection.price !== "number" || Number.isNaN(selection.price); });
            var hasSurvey = selections.some(function (selection) { return selection.payload && selection.payload.is_survey_request; });

            var travelQuote = flowState.travelQuote || getStoredTravelQuote();
            var travelFee = travelQuote && typeof travelQuote.travel_fee === "number" && !Number.isNaN(travelQuote.travel_fee) ? travelQuote.travel_fee : null;

            updateMiniCartTotals(serviceTotal, travelFee, hasCustom, hasSurvey);
        };

        var renderLegacyOptions = function (service, previousSelection, onChange) {
            var options = service.pricing || [];
            if (!options.length) {
                flowOptionsContainer.innerHTML = "<p>Pricing options are not configured yet.</p>";
                onChange(null);
                return;
            }

            var allowMultiselect = service.allow_multiselect === 1 || service.allow_multiselect === true;
            var inputType = allowMultiselect ? "checkbox" : "radio";
            var selectedOptionIds = [];

            // Discount settings
            var threshold = normalizePriceValue(service.discount_threshold) || 0;
            var percent = normalizePriceValue(service.discount_percent) || 0;

            // Restore previous multiselect selections if any
            if (previousSelection && previousSelection.payload && previousSelection.payload.selectedOptions) {
                selectedOptionIds = previousSelection.payload.selectedOptions.map(function (o) { return o.id; });
            } else if (previousSelection && previousSelection.optionId) {
                selectedOptionIds = [previousSelection.optionId];
            }

            // Create discount badge element for multiselect
            var discountBadge = null;
            if (allowMultiselect) {
                discountBadge = document.createElement("div");
                discountBadge.className = "bulk-discount-badge";
                discountBadge.style.cssText = "display:none; margin-top:0.75rem; padding:0.6rem 1rem; background:#ecfdf3; color:#166534; border:1px solid #bbf7d0; border-radius:0.5rem; font-weight:600;";
            }

            var emitMultiselectChange = function () {
                var checkedInputs = flowOptionsContainer.querySelectorAll("input[type='checkbox']:checked");
                var selectedOptions = [];
                var subtotal = 0;
                var labels = [];
                checkedInputs.forEach(function (input) {
                    var opt = options.find(function (o) { return String(o.id) === String(input.value); });
                    if (opt) {
                        selectedOptions.push({ id: opt.id, label: opt.label, price: opt.price });
                        labels.push(opt.label);
                        if (typeof opt.price === "number") {
                            subtotal += opt.price;
                        }
                    }
                });

                if (!selectedOptions.length) {
                    if (discountBadge) discountBadge.style.display = "none";
                    onChange(null);
                    return;
                }

                // Apply bulk discount if threshold is met
                var discountAmount = 0;
                if (threshold > 0 && percent > 0 && subtotal > threshold) {
                    discountAmount = subtotal * (percent / 100);
                }
                var finalPrice = subtotal - discountAmount;

                // Update discount badge
                if (discountBadge) {
                    if (discountAmount > 0) {
                        discountBadge.innerHTML = "✓ Bulk Discount Applied: <span style='text-decoration:line-through; opacity:0.7; margin:0 0.5rem;'>" + formatPrice(subtotal) + "</span> → <strong>" + formatPrice(finalPrice) + "</strong> <span style='color:#15803d;'>(-" + formatPrice(discountAmount) + ")</span>";
                        discountBadge.style.display = "block";
                    } else {
                        discountBadge.style.display = "none";
                    }
                }

                var payload = {
                    optionId: selectedOptions[0].id, // Keep backward compat
                    optionLabel: labels.join(", "),
                    optionDetails: selectedOptions.length + " item(s) selected" + (discountAmount > 0 ? " • " + percent + "% off" : ""),
                    price: finalPrice > 0 ? finalPrice : null,
                    priceDisplay: finalPrice > 0 ? formatPrice(finalPrice) : "Custom quote",
                    modelType: "options",
                    selectedOptions: selectedOptions,
                    payload: {
                        type: "multiselect_options",
                        selectedOptions: selectedOptions,
                        subtotal: subtotal,
                        discount_threshold: threshold,
                        discount_percent: percent,
                        discount_amount: discountAmount,
                        total: finalPrice
                    }
                };
                onChange(payload);
            };

            options.forEach(function (option) {
                var label = document.createElement("label");
                label.className = "flow-option";
                var optionId = service.id + "_" + option.id;

                var input = document.createElement("input");
                input.type = inputType;
                input.name = "flow-option";
                input.value = option.id;
                input.id = optionId;

                var hasMatch = selectedOptionIds.indexOf(option.id) !== -1;
                input.checked = Boolean(hasMatch);

                var meta = document.createElement("div");
                meta.className = "flow-option__meta";
                var details = document.createElement("div");
                details.className = "flow-option__details";
                var strong = document.createElement("strong");
                strong.textContent = option.label;
                details.appendChild(strong);
                if (option.details) {
                    var detail = document.createElement("span");
                    detail.textContent = option.details;
                    details.appendChild(detail);
                }
                meta.appendChild(input);
                meta.appendChild(details);

                var price = document.createElement("div");
                price.className = "flow-option__price";
                price.textContent = typeof option.price === "number" ? formatPrice(option.price) : "Custom quote";

                label.appendChild(meta);
                label.appendChild(price);

                if (allowMultiselect) {
                    input.addEventListener("change", function () {
                        // Toggle selected styling
                        label.classList.toggle("is-selected", input.checked);
                        emitMultiselectChange();
                    });
                } else {
                    input.addEventListener("change", function () {
                        var payload = {
                            optionId: option.id,
                            optionLabel: option.label,
                            optionDetails: option.details || "",
                            price: typeof option.price === "number" ? option.price : null,
                            priceDisplay: typeof option.price === "number" ? formatPrice(option.price) : "Custom quote",
                            modelType: "options"
                        };
                        onChange(payload);
                    });
                }

                if (hasMatch) {
                    label.classList.add("is-selected");
                }

                flowOptionsContainer.appendChild(label);
            });

            // Append discount badge after all options
            if (discountBadge) {
                flowOptionsContainer.appendChild(discountBadge);
            }

            if (allowMultiselect && selectedOptionIds.length) {
                emitMultiselectChange();
            } else if (previousSelection) {
                onChange(previousSelection);
            }
        };

        var renderTenancyConfigurator = function (service, previousSelection, onChange) {
            var rates = Array.isArray(service.tenancy_rates) ? service.tenancy_rates : [];
            if (!rates.length) {
                flowOptionsContainer.innerHTML = "<p>No tenancy rates configured.</p>";
                onChange(null);
                return;
            }

            // Use dynamic headers from service config
            var headerCol1 = service.table_header_col1 || 'Property Type';
            var headerCol2 = service.table_header_col2 || 'Standard Price';
            var headerCol3 = service.table_header_col3 || 'Upgrade Option';

            var selectedRateId = (previousSelection && previousSelection.payload && previousSelection.payload.rate_id) || rates[0].id;
            var deepSelections = {};
            if (previousSelection && previousSelection.payload && previousSelection.payload.rate_id) {
                deepSelections[previousSelection.payload.rate_id] = previousSelection.payload.variant === "deep";
            }

            var rowsMeta = [];

            var table = document.createElement("div");
            table.className = "tenancy-table-grid";

            var header = document.createElement("div");
            header.className = "tenancy-row tenancy-row--header";
            header.innerHTML = '<div class="tenancy-cell">' + escapeHtml(headerCol1) + '</div><div class="tenancy-cell tenancy-cell--center">' + escapeHtml(headerCol2) + '</div><div class="tenancy-cell tenancy-cell--right">' + escapeHtml(headerCol3) + '</div>';
            table.appendChild(header);

            rates.forEach(function (rate) {
                var row = document.createElement("div");
                row.className = "tenancy-row";
                row.setAttribute("data-rate-id", rate.id);

                var standardPrice = normalizePriceValue(rate.standard_price);
                var deepPrice = normalizePriceValue(rate.deep_clean_price);
                var deepChecked = Boolean(deepSelections[rate.id]) && deepPrice !== null;

                var radioId = "tenancy-rate-" + rate.id;
                var propertyCell = document.createElement("label");
                propertyCell.className = "tenancy-cell tenancy-cell--property";
                propertyCell.setAttribute("for", radioId);

                var radio = document.createElement("input");
                radio.type = "radio";
                radio.name = "tenancy-rate";
                radio.id = radioId;
                radio.value = rate.id;
                radio.checked = String(selectedRateId) === String(rate.id);

                var propertyLabel = document.createElement("div");
                propertyLabel.className = "tenancy-property__label";
                propertyLabel.textContent = rate.label || "Property";

                propertyCell.appendChild(radio);
                propertyCell.appendChild(propertyLabel);

                var priceCell = document.createElement("div");
                priceCell.className = "tenancy-cell tenancy-cell--center";
                var priceWrap = document.createElement("div");
                priceWrap.className = "tenancy-price";
                var priceValue = document.createElement("div");
                priceValue.className = "tenancy-price__value";
                var priceLabel = document.createElement("div");
                priceLabel.className = "tenancy-price__label";
                priceWrap.appendChild(priceValue);
                priceWrap.appendChild(priceLabel);
                priceCell.appendChild(priceWrap);

                var upgradeCell = document.createElement("div");
                upgradeCell.className = "tenancy-cell tenancy-cell--right";

                var upgradeLabel = null;
                var deepCheck = null;
                var upgradePrice = null;

                if (!rate.is_blocker) {
                    upgradeLabel = document.createElement("label");
                    upgradeLabel.className = "tenancy-upgrade";
                    deepCheck = document.createElement("input");
                    deepCheck.type = "checkbox";
                    deepCheck.disabled = deepPrice === null || deepPrice === undefined;
                    deepCheck.checked = deepChecked;
                    var upgradeText = document.createElement("div");
                    upgradeText.className = "tenancy-upgrade__label";
                    upgradeText.textContent = deepPrice !== null ? "Add carpet" : "Not available";
                    upgradePrice = document.createElement("div");
                    upgradePrice.className = "tenancy-upgrade__price";
                    upgradeLabel.appendChild(deepCheck);
                    upgradeLabel.appendChild(upgradeText);
                    upgradeLabel.appendChild(upgradePrice);
                    upgradeCell.appendChild(upgradeLabel);
                }

                row.appendChild(propertyCell);
                row.appendChild(priceCell);
                row.appendChild(upgradeCell);

                function selectRate() {
                    selectedRateId = rate.id;
                    if (deepSelections[rate.id] === undefined) {
                        deepSelections[rate.id] = false;
                    }
                    radio.checked = true;
                    updateRowStates();
                    emit();
                }

                radio.addEventListener("change", function () {
                    selectRate();
                });

                row.addEventListener("click", function (event) {
                    if (event.target && event.target.tagName === "INPUT" && event.target.type === "checkbox") {
                        selectRate();
                        return;
                    }
                    if (event.target && event.target.tagName === "INPUT") {
                        return;
                    }
                    selectRate();
                });

                if (deepCheck) {
                    deepCheck.addEventListener("change", function () {
                        deepSelections[rate.id] = deepCheck.checked && !deepCheck.disabled;
                        if (String(selectedRateId) === String(rate.id)) {
                            emit();
                        }
                        updateRowStates();
                    });
                }

                rowsMeta.push({
                    row: row,
                    radio: radio,
                    deepCheck: deepCheck,
                    upgradeLabel: upgradeLabel,
                    upgradePrice: upgradePrice,
                    priceValue: priceValue,
                    priceLabel: priceLabel,
                    isBlocker: Boolean(rate.is_blocker),
                    rateId: rate.id,
                    standardPrice: standardPrice,
                    deepPrice: deepPrice,
                    blockerMsg: rate.blocker_msg || null
                });

                table.appendChild(row);
            });

            flowOptionsContainer.appendChild(table);

            function updateRowStates() {
                rowsMeta.forEach(function (meta) {
                    var isActive = String(meta.rateId) === String(selectedRateId);
                    var deepOn = Boolean(deepSelections[meta.rateId]) && meta.deepPrice !== null;
                    var activePrice = deepOn ? meta.deepPrice : meta.standardPrice;
                    meta.row.classList.toggle("tenancy-row--active", isActive);
                    if (meta.upgradeLabel) {
                        meta.upgradeLabel.classList.toggle("is-active", deepOn);
                    }
                    if (meta.upgradePrice) {
                        meta.upgradePrice.textContent = meta.deepPrice !== null ? "Total " + formatPrice(meta.deepPrice) : "Custom quote";
                    }

                    if (meta.isBlocker) {
                        meta.priceValue.textContent = meta.blockerMsg || "Survey Needed";
                        meta.priceLabel.textContent = "Survey";
                    } else {
                        meta.priceValue.textContent = typeof activePrice === "number" ? formatPrice(activePrice) : (meta.blockerMsg || "Custom quote");
                        meta.priceLabel.textContent = deepOn ? "With carpet" : "Standard clean";
                    }
                });
            }

            function emit() {
                var chosen = rates.find(function (r) { return String(r.id) === String(selectedRateId); }) || rates[0];
                if (!chosen) {
                    onChange(null);
                    return;
                }
                var deepOn = Boolean(deepSelections[chosen.id]) && chosen.deep_clean_price !== null && chosen.deep_clean_price !== undefined;
                var priceValue = deepOn ? normalizePriceValue(chosen.deep_clean_price) : normalizePriceValue(chosen.standard_price);
                var isBlocker = Boolean(chosen.is_blocker);
                var optionDetails = deepOn ? "With carpet (deep)" : "Standard clean";
                if (chosen.blocker_msg) {
                    optionDetails += " • " + chosen.blocker_msg;
                }

                var priceDisplay = typeof priceValue === "number" ? formatPrice(priceValue) : (chosen.blocker_msg || "Custom quote");
                if (isBlocker) {
                    priceValue = null;
                    priceDisplay = chosen.blocker_msg || "Survey Needed";
                }

                var payload = {
                    optionId: chosen.id,
                    optionLabel: chosen.label || "Tenancy",
                    optionDetails: optionDetails,
                    price: priceValue,
                    priceDisplay: priceDisplay,
                    modelType: "tenancy",
                    payload: {
                        type: "tenancy",
                        rate_id: chosen.id,
                        variant: deepOn ? "deep" : "standard",
                        is_blocker: isBlocker,
                        is_survey_request: false
                    }
                };
                onChange(payload);
                updateRowStates();
            }

            updateRowStates();
            // Only auto-select if there was a previous selection, otherwise let user choose
            if (previousSelection && previousSelection.payload && previousSelection.payload.rate_id) {
                emit();
            }
        };

        var renderHourlyBlocksConfigurator = function (service, schema, previousSelection, onChange) {
            var minHours = schema.min_hours || schema.default_hours || 1;
            var maxHours = schema.max_hours || 12;
            var defaultHours = previousSelection && previousSelection.payload && previousSelection.payload.hours ? previousSelection.payload.hours : minHours;
            var rateRow = document.createElement("div");
            rateRow.style.display = "flex";
            rateRow.style.justifyContent = "space-between";
            rateRow.style.alignItems = "center";
            rateRow.style.marginBottom = "0.5rem";
            rateRow.innerHTML = '<span style="font-weight:600;">Hours</span>';

            var hoursInput = document.createElement("input");
            hoursInput.type = "number";
            hoursInput.min = minHours;
            hoursInput.max = maxHours;
            hoursInput.step = 1;
            hoursInput.value = defaultHours;
            hoursInput.style.width = "100px";
            rateRow.appendChild(hoursInput);
            flowOptionsContainer.appendChild(rateRow);

            var discountList = document.createElement("div");
            discountList.style.fontSize = "0.9rem";
            var discountLines = (schema.discounts || []).map(function (rule) {
                return "Save with " + (rule.min_hours || "") + "+ hrs @ " + formatPrice(rule.hourly_rate || schema.hourly_rate) + "/hr";
            });
            if (discountLines.length) {
                discountList.textContent = discountLines.join(" • ");
                discountList.style.color = "#6b7280";
                flowOptionsContainer.appendChild(discountList);
            }

            var addonContainer = document.createElement("div");
            var addonState = (previousSelection && previousSelection.payload && previousSelection.payload.addons) || {};
            if (Array.isArray(schema.addons) && schema.addons.length) {
                addonContainer.style.marginTop = "0.75rem";
                addonContainer.innerHTML = '<p style="margin:0 0 0.35rem; font-weight:600;">Extras</p>';
                schema.addons.forEach(function (addon) {
                    var row = document.createElement("div");
                    row.style.display = "flex";
                    row.style.justifyContent = "space-between";
                    row.style.alignItems = "center";
                    row.style.marginBottom = "0.35rem";

                    var label = document.createElement("span");
                    label.textContent = addon.label + " (" + formatPrice(addon.price || 0) + ")";

                    var input = document.createElement("input");
                    input.type = "number";
                    input.min = 0;
                    input.max = addon.max_qty || 5;
                    input.step = 1;
                    input.value = addonState[addon.id] || 0;
                    input.setAttribute("data-addon-id", addon.id);
                    input.setAttribute("data-addon-price", addon.price || 0);
                    input.style.width = "90px";

                    input.addEventListener("input", function () {
                        if (Number(input.value) < 0) input.value = 0;
                        recalc();
                    });

                    row.appendChild(label);
                    row.appendChild(input);
                    addonContainer.appendChild(row);
                });
                flowOptionsContainer.appendChild(addonContainer);
            }

            var recalc = function () {
                var hours = Number(hoursInput.value) || minHours;
                hours = Math.max(minHours, Math.min(hours, maxHours));
                hoursInput.value = hours;

                var baseRate = normalizePriceValue(schema.hourly_rate) || 0;
                var activeRate = baseRate;
                (schema.discounts || []).forEach(function (rule) {
                    if (rule.hourly_rate && hours >= rule.min_hours) {
                        activeRate = Math.min(activeRate, rule.hourly_rate);
                    }
                });

                var blockSize = schema.block_size || null;
                var blockRate = normalizePriceValue(schema.block_rate) || null;
                var total = 0;
                if (blockSize && blockRate && hours >= blockSize) {
                    var blocks = Math.floor(hours / blockSize);
                    var remainder = hours - (blocks * blockSize);
                    total += blocks * blockRate;
                    total += remainder * activeRate;
                } else {
                    total = hours * activeRate;
                }

                var addonLines = [];
                var addonTotal = 0;
                flowOptionsContainer.querySelectorAll('input[data-addon-id]').forEach(function (input) {
                    var qty = Number(input.value) || 0;
                    var price = Number(input.getAttribute("data-addon-price")) || 0;
                    var addonId = input.getAttribute("data-addon-id");
                    if (qty > 0) {
                        var addonMeta = (schema.addons || []).find(function (a) { return String(a.id) === String(addonId); }) || {};
                        addonLines.push(qty + " × " + (addonMeta.label || "Addon"));
                        addonTotal += qty * price;
                    }
                    addonState[addonId] = qty;
                });

                var grand = total + addonTotal;
                var details = [hours + " hrs @ " + formatPrice(activeRate) + "/hr"];
                if (addonLines.length) details.push(addonLines.join(", "));

                var payload = {
                    optionId: null,
                    optionLabel: "Hourly package",
                    optionDetails: details.join(" • "),
                    price: grand,
                    priceDisplay: grand !== null ? formatPrice(grand) : "Custom quote",
                    modelType: "hourly_blocks",
                    payload: { hours: hours, addons: addonState }
                };
                onChange(payload);
            };

            recalc();
        };

        var renderDeepTierConfigurator = function (service, previousSelection, onChange) {
            var tiers = Array.isArray(service.pricing_tiers) ? service.pricing_tiers : [];
            if (!tiers.length) {
                flowOptionsContainer.innerHTML = "<p>No deep cleaning tiers configured.</p>";
                onChange(null);
                return;
            }

            var selectedId = (previousSelection && previousSelection.payload && previousSelection.payload.tier_id) || tiers[0].id;
            var previousStaff = previousSelection && previousSelection.payload ? previousSelection.payload.staff : null;
            var previousHours = previousSelection && previousSelection.payload ? previousSelection.payload.hours : null;
            var previousRooms = (previousSelection && previousSelection.payload && previousSelection.payload.rooms) || {};

            // ── Tier list (same style as Airbnb) ──────────────────────────────
            var listWrap = document.createElement('div');
            listWrap.style.cssText = 'display:flex;flex-direction:column;gap:0.5rem;margin-bottom:1rem;';

            tiers.forEach(function (tier) {
                var minS = Math.max(Number(tier.min_staff) || 1, 1);
                var rate = normalizePriceValue(tier.hourly_rate);
                var label = document.createElement('button');
                label.type = 'button';
                label.className = 'flow-option';
                label.setAttribute('data-tier-id', tier.id);
                var hasRooms = Array.isArray(tier.rooms_config) && tier.rooms_config.length > 0;
                var fixedS = tier.fixed_staff != null ? Number(tier.fixed_staff) : null;
                var hint = hasRooms
                    ? (rate !== null ? formatPrice(rate) + '/hr' : '')
                    : (fixedS != null ? fixedS + ' staff (fixed)' : 'Min ' + minS + ' staff') + (rate !== null ? ' • From ' + formatPrice(rate) + '/hr' : '');
                label.innerHTML =
                    '<div style="display:flex;justify-content:space-between;align-items:center;gap:0.5rem;">' +
                    '<strong>' + escapeHtml(tier.tier_name || 'Deep clean') + '</strong>' +
                    '<span style="font-size:0.85rem;color:#6b7280;">' + hint + '</span>' +
                    '</div>';
                label.addEventListener('click', function () { selectTier(tier.id); });
                listWrap.appendChild(label);
            });

            // ── Controls area (staff hidden in rooms mode, hours/rooms) ──────
            var controls = document.createElement('div');
            controls.style.cssText = 'display:grid;gap:0.75rem;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));margin-top:0.5rem;';

            var _initTier = tiers.find(function(t) { return String(t.id) === String(selectedId); }) || tiers[0];
            var _initHasRooms = Array.isArray(_initTier && _initTier.rooms_config) && _initTier.rooms_config.length > 0;
            var _initMin = Math.max(Number(_initTier && _initTier.min_staff) || 1, 1);
            var _initMinHours = Math.max(Number(_initTier && _initTier.min_hours) || 1, 0.5);
            var _initFixed = _initTier && _initTier.fixed_staff != null ? Number(_initTier.fixed_staff) : null;
            var _initFixedHours = _initTier && _initTier.fixed_hours != null ? Number(_initTier.fixed_hours) : null;

            // Staff input — hidden when rooms_config present (staff decided internally)
            var staffLabelEl = document.createElement('label');
            staffLabelEl.style.cssText = 'display:flex;flex-direction:column;gap:0.35rem;';
            var staffLabelText = document.createElement('span');
            staffLabelEl.appendChild(staffLabelText);
            var staffInput = document.createElement('input');
            staffInput.type = 'number';
            staffInput.step = 1;
            staffInput.min = _initFixed != null ? _initFixed : _initMin;
            staffInput.value = _initFixed != null ? _initFixed : (previousStaff ? Math.max(Number(previousStaff), _initMin) : _initMin);
            if (_initFixed != null) { staffInput.readOnly = true; staffInput.style.background = '#f1f5f9'; staffInput.style.cursor = 'not-allowed'; }
            staffLabelText.textContent = (_initTier && _initTier.staff_label || 'Number of Staff') + (_initFixed != null ? ' (fixed: ' + _initFixed + ')' : ' (min ' + _initMin + ')');
            staffLabelEl.appendChild(staffInput);
            if (!_initHasRooms) controls.appendChild(staffLabelEl);

            // Hours input (shown when no rooms_config)
            var hoursLabelEl = document.createElement('label');
            hoursLabelEl.style.cssText = 'display:flex;flex-direction:column;gap:0.35rem;';
            var hoursLabelText = document.createElement('span');
            hoursLabelEl.appendChild(hoursLabelText);
            var hoursInput = document.createElement('input');
            hoursInput.type = 'number';
            hoursInput.step = 0.5;
            hoursInput.min = _initFixedHours != null ? _initFixedHours : _initMinHours;
            hoursInput.value = _initFixedHours != null ? _initFixedHours : (previousHours ? Math.max(Number(previousHours), _initMinHours) : _initMinHours);
            if (_initFixedHours != null) { hoursInput.readOnly = true; hoursInput.style.background = '#f1f5f9'; hoursInput.style.cursor = 'not-allowed'; }
            hoursLabelText.textContent = (_initTier && _initTier.hours_label || 'Hours Required') + (_initFixedHours != null ? ' (fixed: ' + _initFixedHours + ')' : ' (min ' + _initMinHours + ')');
            hoursLabelEl.appendChild(hoursInput);

            // Rooms section (shown when tier has rooms_config)
            var roomsSection = document.createElement('div');
            roomsSection.style.cssText = 'grid-column:1/-1;display:none;flex-direction:column;gap:0.5rem;margin-top:0.25rem;';
            var roomsTitle = document.createElement('div');
            roomsTitle.style.cssText = 'font-size:0.85rem;font-weight:600;color:#374151;margin-bottom:0.1rem;';
            roomsTitle.textContent = 'Select Rooms';
            roomsSection.appendChild(roomsTitle);
            var roomsGrid = document.createElement('div');
            roomsGrid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:0.5rem;';
            roomsSection.appendChild(roomsGrid);
            var roomsNote = document.createElement('div');
            roomsNote.style.cssText = 'font-size:0.8rem;color:#6b7280;margin-top:0.15rem;';
            roomsSection.appendChild(roomsNote);

            var summary = document.createElement('div');
            summary.className = 'price-summary-box';
            summary.style.marginTop = '0.85rem';

            flowOptionsContainer.appendChild(listWrap);
            flowOptionsContainer.appendChild(controls);
            flowOptionsContainer.appendChild(roomsSection);
            flowOptionsContainer.appendChild(summary);

            var roomQty = {}; // { roomName: qty }

            var buildRoomsUI = function (tier) {
                roomsGrid.innerHTML = '';
                var rc = Array.isArray(tier.rooms_config) ? tier.rooms_config : null;
                if (!rc || !rc.length) {
                    // No rooms — show staff + hours inputs
                    roomsSection.style.display = 'none';
                    if (!staffLabelEl.parentNode) controls.insertBefore(staffLabelEl, controls.firstChild);
                    if (!hoursLabelEl.parentNode) controls.appendChild(hoursLabelEl);
                    return;
                }
                // Rooms mode — hide staff input and hours input, show rooms picker
                if (staffLabelEl.parentNode) staffLabelEl.parentNode.removeChild(staffLabelEl);
                if (hoursLabelEl.parentNode) hoursLabelEl.parentNode.removeChild(hoursLabelEl);
                roomsSection.style.display = 'flex';

                rc.forEach(function (room) {
                    var name = room.name || room.label || 'Room';
                    var hrs = Number(room.hours) || 0.5;
                    if (!(name in roomQty)) roomQty[name] = previousRooms[name] || 0;

                    var row = document.createElement('div');
                    row.style.cssText = 'display:flex;align-items:center;justify-content:space-between;background:#f8fafc;border:1px solid #e5e7eb;border-radius:0.5rem;padding:0.5rem 0.75rem;gap:0.5rem;';

                    var nameEl = document.createElement('div');
                    nameEl.style.cssText = 'font-size:0.875rem;font-weight:500;flex:1;';
                    nameEl.textContent = name;
                    var hrsEl = document.createElement('div');
                    hrsEl.style.cssText = 'font-size:0.75rem;color:#6b7280;';
                    hrsEl.textContent = hrs + 'hr';

                    var stepper = document.createElement('div');
                    stepper.style.cssText = 'display:flex;align-items:center;gap:0.3rem;';
                    var minusBtn = document.createElement('button');
                    minusBtn.type = 'button';
                    minusBtn.textContent = '−';
                    minusBtn.style.cssText = 'width:26px;height:26px;border-radius:50%;border:1px solid #d1d5db;background:#fff;cursor:pointer;font-size:1rem;line-height:1;display:flex;align-items:center;justify-content:center;';
                    var qtyEl = document.createElement('span');
                    qtyEl.style.cssText = 'min-width:18px;text-align:center;font-weight:600;font-size:0.9rem;';
                    qtyEl.textContent = roomQty[name];
                    var plusBtn = document.createElement('button');
                    plusBtn.type = 'button';
                    plusBtn.textContent = '+';
                    plusBtn.style.cssText = minusBtn.style.cssText;

                    minusBtn.addEventListener('click', function () {
                        roomQty[name] = Math.max(0, roomQty[name] - 1);
                        qtyEl.textContent = roomQty[name];
                        emit();
                    });
                    plusBtn.addEventListener('click', function () {
                        roomQty[name] = (roomQty[name] || 0) + 1;
                        qtyEl.textContent = roomQty[name];
                        emit();
                    });

                    stepper.appendChild(minusBtn);
                    stepper.appendChild(qtyEl);
                    stepper.appendChild(plusBtn);
                    row.appendChild(nameEl);
                    row.appendChild(hrsEl);
                    row.appendChild(stepper);
                    roomsGrid.appendChild(row);
                });
            };

            var selectTier = function (tierId) {
                selectedId = tierId;
                Array.from(listWrap.querySelectorAll('.flow-option')).forEach(function (row) {
                    row.classList.toggle('is-selected', String(row.getAttribute('data-tier-id')) === String(tierId));
                });
                var tier = tiers.find(function (t) { return String(t.id) === String(selectedId); }) || tiers[0];
                var minS = Math.max(Number(tier.min_staff) || 1, 1);
                var minH = Math.max(Number(tier.min_hours) || 1, 0.5);
                var fixS = tier.fixed_staff != null ? Number(tier.fixed_staff) : null;
                var fixH = tier.fixed_hours != null ? Number(tier.fixed_hours) : null;

                if (fixS != null) {
                    staffInput.readOnly = true; staffInput.style.background = '#f1f5f9'; staffInput.style.cursor = 'not-allowed';
                    staffInput.value = fixS; staffInput.min = fixS;
                    staffLabelText.textContent = (tier.staff_label || 'Number of Staff') + ' (fixed: ' + fixS + ')';
                } else {
                    staffInput.readOnly = false; staffInput.style.background = ''; staffInput.style.cursor = '';
                    staffInput.min = minS;
                    staffLabelText.textContent = (tier.staff_label || 'Number of Staff') + ' (min ' + minS + ')';
                    if (!Number.isFinite(Number(staffInput.value)) || Number(staffInput.value) < minS) staffInput.value = minS;
                }

                if (fixH != null) {
                    hoursInput.readOnly = true; hoursInput.style.background = '#f1f5f9'; hoursInput.style.cursor = 'not-allowed';
                    hoursInput.value = fixH; hoursInput.min = fixH;
                    hoursLabelText.textContent = (tier.hours_label || 'Hours Required') + ' (fixed: ' + fixH + ')';
                } else {
                    hoursInput.readOnly = false; hoursInput.style.background = ''; hoursInput.style.cursor = '';
                    hoursInput.min = minH;
                    hoursLabelText.textContent = (tier.hours_label || 'Hours Required') + ' (min ' + minH + ')';
                    if (!Number.isFinite(Number(hoursInput.value)) || Number(hoursInput.value) < minH) hoursInput.value = minH;
                }

                buildRoomsUI(tier);
                emit();
            };

            function emit() {
                var tier = tiers.find(function (t) { return String(t.id) === String(selectedId); }) || tiers[0];
                var fixS = tier.fixed_staff != null ? Number(tier.fixed_staff) : null;
                var fixH = tier.fixed_hours != null ? Number(tier.fixed_hours) : null;
                var minS = Math.max(Number(tier.min_staff) || 1, 1);
                var minH = Math.max(Number(tier.min_hours) || 1, 0.5);
                var staff = fixS != null ? fixS : Math.max(Number(staffInput.value) || minS, minS);
                var rc = Array.isArray(tier.rooms_config) ? tier.rooms_config : null;

                var hours;
                if (rc && rc.length) {
                    // Hours derived purely from rooms — no min_hours floor
                    hours = 0;
                    rc.forEach(function (room) {
                        var name = room.name || room.label || 'Room';
                        hours += (roomQty[name] || 0) * (Number(room.hours) || 0.5);
                    });
                    var roomSummary = rc.filter(function(r){ return (roomQty[r.name||r.label||'Room']||0) > 0; })
                        .map(function(r){ var n=r.name||r.label||'Room'; return roomQty[n]+'× '+n; }).join(', ');
                    roomsNote.textContent = roomSummary ? 'Selected: ' + roomSummary + ' → ' + hours + ' hrs' : 'Select rooms above to calculate price';
                } else {
                    hours = fixH != null ? fixH : Math.max(Number(hoursInput.value) || minH, minH);
                }

                var rate = normalizePriceValue(tier.hourly_rate);
                var equipment = normalizePriceValue(tier.equipment_fee) || 0;
                var detergent = normalizePriceValue(tier.detergent_fee) || 0;
                var materialsTotal = equipment + detergent;
                // Rooms mode: price = rate × hours (staff handled internally, not shown)
                // Non-rooms mode: price = rate × staff × hours
                var hasRoomsMode = rc && rc.length;
                var total = (rate !== null && (!hasRoomsMode || hours > 0))
                    ? Math.round(((hasRoomsMode ? rate * hours : rate * staff * hours) + materialsTotal) * 100) / 100
                    : null;

                if (hasRoomsMode && hours === 0) {
                    summary.innerHTML = '<div style="color:#6b7280;font-size:0.9rem;text-align:center;padding:0.5rem 0;">Select rooms above to see your price.</div>';
                } else if (hasRoomsMode) {
                    summary.innerHTML = rate !== null ? [
                        '<div class="row"><span>Hours:</span><span>' + hours + ' hrs</span></div>',
                        '<div class="row"><span>Price per hour:</span><span>' + formatPrice(rate) + '</span></div>',
                        equipment > 0 ? '<div class="row"><span>Equipment fee:</span><span>' + formatPrice(equipment) + '</span></div>' : '',
                        detergent > 0 ? '<div class="row"><span>Materials:</span><span>' + formatPrice(detergent) + '</span></div>' : '',
                        '<div class="total-row"><span>ESTIMATED TOTAL:</span><span>' + formatPrice(total) + '</span></div>'
                    ].join('') : 'Custom quote';
                } else {
                    summary.innerHTML = rate !== null ? [
                        '<div class="row"><span>Package:</span><span>' + escapeHtml(tier.tier_name || 'Deep clean') + '</span></div>',
                        '<div class="row"><span>Rate:</span><span>' + formatPrice(rate) + ' /hr per cleaner</span></div>',
                        '<div class="row"><span>Staff:</span><span>' + staff + '</span></div>',
                        '<div class="row"><span>Hours:</span><span>' + hours + '</span></div>',
                        materialsTotal > 0 ? '<div class="row"><span>Materials:</span><span>' + formatPrice(materialsTotal) + '</span></div>' : '',
                        '<div class="total-row"><span>ESTIMATED TOTAL:</span><span>' + formatPrice(total) + '</span></div>'
                    ].join('') : 'Custom quote';
                }

                onChange({
                    optionId: tier ? tier.id : null,
                    optionLabel: tier ? (tier.tier_name || 'Deep clean') : 'Deep clean',
                    optionDetails: hasRoomsMode ? 'Hours: ' + hours : 'Staff: ' + staff + ' • Hours: ' + hours,
                    price: total,
                    priceDisplay: total !== null ? formatPrice(total) : (hasRoomsMode ? 'Select rooms' : 'Custom quote'),
                    modelType: 'deep',
                    payload: {
                        type: 'deep',
                        tier_id: tier ? tier.id : null,
                        staff: staff,
                        hours: hours,
                        rooms: rc ? Object.assign({}, roomQty) : undefined,
                        equipment_fee: equipment,
                        detergent_fee: detergent
                    }
                });
            }

            staffInput.addEventListener('input', emit);
            hoursInput.addEventListener('input', emit);
            selectTier(selectedId);
            // Only auto-select if there was a previous selection, otherwise let user choose
            if (previousSelection && previousSelection.payload && previousSelection.payload.tier_id) {
                emit();
            }
        };

        var renderSurveyConfigurator = function (service, onChange) {
            flowOptionsContainer.innerHTML = '';

            var wrap = document.createElement('div');
            wrap.style.cssText = 'border:1px solid #e5e7eb;border-radius:0.75rem;overflow:hidden;';

            var header = document.createElement('div');
            header.style.cssText = 'background:#1e3a5f;color:#fff;padding:1.25rem 1.5rem;';
            header.innerHTML = '<div style="font-size:1.05rem;font-weight:700;margin-bottom:0.3rem;">Survey Required</div>' +
                '<div style="font-size:0.85rem;opacity:0.85;">This service is priced individually after an on-site survey by one of our specialists.</div>';

            var body = document.createElement('div');
            body.style.cssText = 'background:#f8fafc;padding:1.25rem 1.5rem;display:flex;flex-direction:column;gap:0.75rem;';

            var points = [
                'We visit your property to assess the scope of work',
                'You receive a tailored, no-obligation quote',
                'No payment is taken until you approve the quote'
            ];
            var list = document.createElement('ul');
            list.style.cssText = 'margin:0;padding-left:1.25rem;display:flex;flex-direction:column;gap:0.4rem;';
            points.forEach(function(p) {
                var li = document.createElement('li');
                li.style.cssText = 'font-size:0.9rem;color:#374151;';
                li.textContent = p;
                list.appendChild(li);
            });

            var priceNote = document.createElement('div');
            priceNote.style.cssText = 'background:#fff;border:1px solid #e5e7eb;border-radius:0.5rem;padding:0.75rem 1rem;font-size:0.9rem;color:#6b7280;text-align:center;';
            priceNote.textContent = 'Price: To be confirmed after survey';

            body.appendChild(list);
            body.appendChild(priceNote);
            wrap.appendChild(header);
            wrap.appendChild(body);
            flowOptionsContainer.appendChild(wrap);

            onChange({
                optionId: 'survey',
                optionLabel: 'Survey Required',
                optionDetails: 'Price confirmed after on-site assessment',
                price: null,
                priceDisplay: 'Survey Required',
                modelType: 'survey',
                payload: {
                    type: 'survey',
                    is_survey_request: true
                }
            });
        };

        var renderAirbnbConfigurator = function (service, previousSelection, onChange) {
            var tiers = Array.isArray(service.pricing_tiers) ? service.pricing_tiers : [];
            if (!tiers.length) {
                flowOptionsContainer.innerHTML = "<p>No Airbnb packages configured.</p>";
                onChange(null);
                return;
            }

            var selectedId = (previousSelection && previousSelection.payload && previousSelection.payload.tier_id) || tiers[0].id;
            var previousStaff = previousSelection && previousSelection.payload ? previousSelection.payload.staff : null;
            var previousHours = previousSelection && previousSelection.payload ? previousSelection.payload.hours : null;

            var listWrap = document.createElement("div");
            listWrap.style.display = "grid";
            listWrap.style.gap = "0.6rem";

            tiers.forEach(function (tier) {
                var label = document.createElement("label");
                label.className = "flow-option";
                label.setAttribute("data-tier-id", tier.id);

                var meta = document.createElement("div");
                meta.className = "flow-option__meta";

                var radio = document.createElement("input");
                radio.type = "radio";
                radio.name = "airbnb-tier";
                radio.value = tier.id;
                radio.checked = String(selectedId) === String(tier.id);

                var details = document.createElement("div");
                details.className = "flow-option__details";

                var strong = document.createElement("strong");
                strong.textContent = tier.tier_name || "Airbnb package";
                details.appendChild(strong);

                var sub = document.createElement("span");
                var minStaff = Number(tier.min_staff) || 1;
                sub.textContent = "Min " + minStaff + " staff";
                details.appendChild(sub);

                meta.appendChild(radio);
                meta.appendChild(details);

                var price = document.createElement("div");
                price.className = "flow-option__price";
                var rate = normalizePriceValue(tier.hourly_rate);
                price.textContent = typeof rate === "number" ? formatPrice(rate) + " /hr per cleaner" : "Custom quote";

                label.appendChild(meta);
                label.appendChild(price);

                radio.addEventListener("change", function () {
                    selectedId = tier.id;
                    emit();
                });

                label.addEventListener("click", function (event) {
                    if (event.target && event.target.tagName === "INPUT") {
                        return;
                    }
                    radio.checked = true;
                    selectedId = tier.id;
                    emit();
                });

                listWrap.appendChild(label);
            });

            var controls = document.createElement("div");
            controls.style.display = "grid";
            controls.style.gap = "0.75rem";
            controls.style.gridTemplateColumns = "repeat(auto-fit, minmax(180px, 1fr))";
            controls.style.marginTop = "1rem";

            var _initTier = tiers.find(function(t) { return String(t.id) === String(selectedId); }) || tiers[0];
            var _initMin = Math.max(Number(_initTier && _initTier.min_staff) || 1, 1);
            var _initMinHours = Math.max(Number(_initTier && _initTier.min_hours) || 1, 0.5);
            var _initFixed = _initTier && _initTier.fixed_staff != null ? Number(_initTier.fixed_staff) : null;
            var _initFixedHours = _initTier && _initTier.fixed_hours != null ? Number(_initTier.fixed_hours) : null;

            var staffLabel = document.createElement("label");
            staffLabel.style.display = "flex";
            staffLabel.style.flexDirection = "column";
            staffLabel.style.gap = "0.35rem";
            var staffLabelText = document.createElement("span");
            staffLabel.appendChild(staffLabelText);
            var staffInput = document.createElement("input");
            staffInput.type = "number";
            staffInput.step = 1;
            staffInput.min = _initFixed != null ? _initFixed : _initMin;
            staffInput.value = _initFixed != null ? _initFixed : (previousStaff ? Math.max(Number(previousStaff), _initMin) : _initMin);
            if (_initFixed != null) { staffInput.readOnly = true; staffInput.style.background = '#f1f5f9'; staffInput.style.cursor = 'not-allowed'; }
            staffLabelText.textContent = (_initTier && _initTier.staff_label || "Number of Staff") + (_initFixed != null ? " (fixed: " + _initFixed + ")" : " (min " + _initMin + ")");
            staffLabel.appendChild(staffInput);

            var hoursLabel = document.createElement("label");
            hoursLabel.style.display = "flex";
            hoursLabel.style.flexDirection = "column";
            hoursLabel.style.gap = "0.35rem";
            var hoursLabelText = document.createElement("span");
            hoursLabel.appendChild(hoursLabelText);
            var hoursInput = document.createElement("input");
            hoursInput.type = "number";
            hoursInput.step = 0.5;
            hoursInput.min = _initFixedHours != null ? _initFixedHours : _initMinHours;
            hoursInput.value = _initFixedHours != null ? _initFixedHours : (previousHours ? Math.max(Number(previousHours), _initMinHours) : _initMinHours);
            if (_initFixedHours != null) { hoursInput.readOnly = true; hoursInput.style.background = '#f1f5f9'; hoursInput.style.cursor = 'not-allowed'; }
            hoursLabelText.textContent = (_initTier && _initTier.hours_label || "Hours Required") + (_initFixedHours != null ? " (fixed: " + _initFixedHours + ")" : " (min " + _initMinHours + ")");
            hoursLabel.appendChild(hoursInput);

            controls.appendChild(staffLabel);
            controls.appendChild(hoursLabel);

            var summary = document.createElement("div");
            summary.className = "price-summary-box";
            summary.style.marginTop = "0.85rem";

            flowOptionsContainer.appendChild(listWrap);
            flowOptionsContainer.appendChild(controls);
            flowOptionsContainer.appendChild(summary);

            function emit() {
                Array.from(listWrap.querySelectorAll('.flow-option')).forEach(function (row) {
                    var rowId = row.getAttribute('data-tier-id');
                    row.classList.toggle('is-selected', String(rowId) === String(selectedId));
                });

                var tier = tiers.find(function (t) { return String(t.id) === String(selectedId); }) || tiers[0];
                var minStaff = Math.max(Number(tier.min_staff) || 1, 1);
                var minHours = Math.max(Number(tier.min_hours) || 1, 0.5);
                var fixedStaff = tier.fixed_staff != null ? Number(tier.fixed_staff) : null;
                var fixedHours = tier.fixed_hours != null ? Number(tier.fixed_hours) : null;

                // Staff input
                if (fixedStaff != null) {
                    staffInput.readOnly = true;
                    staffInput.style.background = '#f1f5f9';
                    staffInput.style.cursor = 'not-allowed';
                    staffInput.value = fixedStaff;
                    staffInput.min = fixedStaff;
                    staffLabelText.textContent = (tier.staff_label || "Number of Staff") + " (fixed: " + fixedStaff + ")";
                } else {
                    staffInput.readOnly = false;
                    staffInput.style.background = '';
                    staffInput.style.cursor = '';
                    staffInput.min = minStaff;
                    staffLabelText.textContent = (tier.staff_label || "Number of Staff") + " (min " + minStaff + ")";
                    if (!Number.isFinite(Number(staffInput.value)) || Number(staffInput.value) < minStaff) {
                        staffInput.value = minStaff;
                    }
                }

                // Hours input
                if (fixedHours != null) {
                    hoursInput.readOnly = true;
                    hoursInput.style.background = '#f1f5f9';
                    hoursInput.style.cursor = 'not-allowed';
                    hoursInput.value = fixedHours;
                    hoursInput.min = fixedHours;
                    hoursLabelText.textContent = (tier.hours_label || "Hours Required") + " (fixed: " + fixedHours + ")";
                } else {
                    hoursInput.readOnly = false;
                    hoursInput.style.background = '';
                    hoursInput.style.cursor = '';
                    hoursInput.min = minHours;
                    hoursLabelText.textContent = (tier.hours_label || "Hours Required") + " (min " + minHours + ")";
                    if (!Number.isFinite(Number(hoursInput.value)) || Number(hoursInput.value) < minHours) {
                        hoursInput.value = minHours;
                    }
                }

                var staff = Number(staffInput.value);
                var hours = Number(hoursInput.value);

                var rate = normalizePriceValue(tier.hourly_rate);
                var equipment = normalizePriceValue(tier.equipment_fee) || 0;
                var detergent = normalizePriceValue(tier.detergent_fee) || 0;
                var total = rate !== null ? Math.round(((rate * staff * hours) + equipment + detergent) * 100) / 100 : null;

                summary.innerHTML = rate !== null ? [
                    '<div class="row"><span>Package:</span><span>' + escapeHtml(tier.tier_name || 'Airbnb package') + '</span></div>',
                    '<div class="row"><span>Rate:</span><span>' + formatPrice(rate) + ' /hr per cleaner</span></div>',
                    '<div class="row"><span>Staff:</span><span>' + staff + '</span></div>',
                    '<div class="row"><span>Hours:</span><span>' + hours + '</span></div>',
                    '<div class="total-row"><span>ESTIMATED TOTAL:</span><span>' + formatPrice(total) + '</span></div>'
                ].join('') : 'Custom quote';

                onChange({
                    optionId: tier ? tier.id : null,
                    optionLabel: tier ? (tier.tier_name || 'Airbnb package') : 'Airbnb package',
                    optionDetails: 'Staff: ' + staff + ' • Hours: ' + hours,
                    price: total,
                    priceDisplay: typeof total === 'number' ? formatPrice(total) : 'Custom quote',
                    modelType: 'airbnb',
                    payload: {
                        type: 'airbnb',
                        tier_id: tier ? tier.id : null,
                        staff: staff,
                        hours: hours,
                        equipment_fee: equipment,
                        detergent_fee: detergent
                    }
                });
            }

            staffInput.addEventListener('input', emit);
            hoursInput.addEventListener('input', emit);
            emit();
        };

        var renderItemizedConfigurator = function (service, schema, previousSelection, onChange) {
            var items = Array.isArray(schema.items) ? schema.items : [];
            if (!items.length) {
                flowOptionsContainer.innerHTML = "<p>Items are not configured yet.</p>";
                onChange(null);
                return;
            }

            var qtyState = (previousSelection && previousSelection.payload && previousSelection.payload.quantities) || {};
            var threshold = normalizePriceValue(service.discount_threshold) || 0;
            var percent = normalizePriceValue(service.discount_percent) || 0;

            items.forEach(function (item) {
                var row = document.createElement("div");
                row.style.display = "grid";
                row.style.gridTemplateColumns = "1fr auto";
                row.style.alignItems = "center";
                row.style.gap = "0.75rem";
                row.style.marginBottom = "0.5rem";

                var label = document.createElement("div");
                label.innerHTML = "<strong>" + item.item_name + "</strong><span style='color:#6b7280; display:block;'>" + formatPrice(item.price || 0) + "</span>";

                var controls = document.createElement("div");
                controls.style.display = "inline-flex";
                controls.style.alignItems = "center";
                controls.style.gap = "0.5rem";

                var minus = document.createElement("button");
                minus.type = "button";
                minus.textContent = "–";
                minus.className = "btn btn-sm";
                var qty = document.createElement("input");
                qty.type = "number";
                qty.min = 0;
                qty.step = 1;
                qty.value = qtyState[item.id] || 0;
                qty.style.width = "70px";
                var plus = document.createElement("button");
                plus.type = "button";
                plus.textContent = "+";
                plus.className = "btn btn-sm";

                minus.addEventListener("click", function () {
                    var next = Math.max(0, Number(qty.value) - 1);
                    qty.value = next;
                    recalc();
                });
                plus.addEventListener("click", function () {
                    qty.value = Number(qty.value) + 1;
                    recalc();
                });
                qty.addEventListener("input", function () {
                    if (Number(qty.value) < 0) qty.value = 0;
                    recalc();
                });

                controls.appendChild(minus);
                controls.appendChild(qty);
                controls.appendChild(plus);

                row.appendChild(label);
                row.appendChild(controls);
                row.querySelectorAll("input,button").forEach(function (el) {
                    el.setAttribute("data-item-id", item.id);
                    el.setAttribute("data-item-price", item.price || 0);
                    el.setAttribute("data-item-name", item.item_name || "Item");
                });
                flowOptionsContainer.appendChild(row);
            });

            var badge = document.createElement("div");
            badge.style.display = "none";
            badge.style.marginTop = "0.35rem";
            badge.style.padding = "0.5rem 0.75rem";
            badge.style.background = "#ecfdf3";
            badge.style.color = "#166534";
            badge.style.border = "1px solid #bbf7d0";
            badge.style.borderRadius = "0.5rem";
            flowOptionsContainer.appendChild(badge);

            function recalc() {
                var subtotal = 0;
                var detailLines = [];
                flowOptionsContainer.querySelectorAll('input[data-item-id]').forEach(function (input) {
                    var qty = Number(input.value) || 0;
                    var price = Number(input.getAttribute("data-item-price")) || 0;
                    var itemId = input.getAttribute("data-item-id");
                    var itemName = input.getAttribute("data-item-name") || "Item";
                    if (qty > 0) {
                        detailLines.push(qty + " × " + itemName);
                        subtotal += qty * price;
                    }
                    qtyState[itemId] = qty;
                });

                if (subtotal <= 0) {
                    badge.style.display = "none";
                    onChange(null);
                    return;
                }

                var discountAmount = 0;
                if (threshold > 0 && percent > 0 && subtotal > threshold) {
                    discountAmount = subtotal * (percent / 100);
                }
                var total = subtotal - discountAmount;
                if (discountAmount > 0) {
                    badge.innerHTML = "✓ Bulk Discount Applied: <span style='text-decoration:line-through; opacity:0.7; margin:0 0.5rem;'>" + formatPrice(subtotal) + "</span> → <strong>" + formatPrice(total) + "</strong> <span style='color:#15803d;'>(-" + formatPrice(discountAmount) + ")</span>";
                    badge.style.display = "block";
                } else {
                    badge.textContent = "";
                    badge.style.display = "none";
                }

                var payload = {
                    optionId: null,
                    optionLabel: "Custom selection",
                    optionDetails: detailLines.join(", ") + (discountAmount > 0 ? " • " + percent + "% off" : ""),
                    price: total,
                    priceDisplay: discountAmount > 0 ? formatPrice(subtotal) + " → " + formatPrice(total) : formatPrice(total),
                    modelType: "itemized",
                    payload: {
                        type: "itemized",
                        quantities: qtyState,
                        subtotal: subtotal,
                        discount_threshold: threshold,
                        discount_percent: percent,
                        discount_amount: discountAmount,
                        total: total
                    }
                };
                onChange(payload);
            }

            recalc();
        };

        var setActiveStep = function (stepNumber) {
            activeStep = stepNumber;
            // Always hide the domestic configurator when switching to a numbered step
            var domesticEl = document.getElementById("flow-domestic-step");
            if (domesticEl) {
                domesticEl.style.display = "none";
                domesticEl.classList.remove("is-active");
            }
            flowSteps.forEach(function (section) {
                var sectionStep = Number(section.getAttribute("data-flow-step"));
                if (isNaN(sectionStep)) return; // skip non-numeric steps (domestic)
                section.classList.toggle("is-active", sectionStep === stepNumber);
                section.style.display = (sectionStep === stepNumber) ? "" : "none";
            });
            flowIndicators.forEach(function (indicator) {
                var indicatorStep = Number(indicator.getAttribute("data-flow-step-indicator"));
                indicator.classList.toggle("is-active", indicatorStep === stepNumber);
            });
            if (stepNumber === 3) {
                updateContractFrequencyVisibility();
            }
        };

        var hydrateContactFields = function () {
            if (flowNameInput) flowNameInput.value = flowState.customer.name || "";
            if (flowEmailInput) flowEmailInput.value = flowState.customer.email || "";
            if (flowPhoneInput) flowPhoneInput.value = flowState.customer.phone || "";
            // Pre-fill location with stored postcode/area if location is empty
            var locationValue = flowState.customer.location || flowState.customer.postcode || getStoredPostcode() || "";
            if (!flowState.customer.location && locationValue) {
                flowState.customer.location = locationValue;
                persistFlowState();
            }
            if (flowLocationInput) flowLocationInput.value = locationValue;
            var hydratedPostcode = flowState.customer.postcode || getStoredPostcode() || "";
            if (!flowState.customer.postcode && hydratedPostcode) {
                flowState.customer.postcode = hydratedPostcode;
                persistFlowState();
            }
            if (flowPostcodeInput) flowPostcodeInput.value = hydratedPostcode;
            if (flowSummaryPostcodeInput) flowSummaryPostcodeInput.value = hydratedPostcode;
            if (flowDateInput) flowDateInput.value = flowState.schedule.preferred_date || "";
            if (flowTimeInput) flowTimeInput.value = flowState.schedule.preferred_time || "";
            if (flowContractFrequencyInput) flowContractFrequencyInput.value = flowState.schedule.contract_frequency || "";
            if (flowContractSignerInput) flowContractSignerInput.value = flowState.contract.signer_name || flowState.customer.name || "";
            if (flowContractTermsInput) flowContractTermsInput.checked = Boolean(flowState.contract.agreed);
            if (flowNotesInput) flowNotesInput.value = flowState.notes || "";
            updateContractFrequencyVisibility();
        };

        var renderServiceStep = function () {
            hideChoicePrompt();
            var service = getServiceFromQueue(currentServiceIndex);
            if (!service || !flowOptionsContainer) {
                return;
            }
            flowServiceIndex.textContent = currentServiceIndex + 1;
            flowServiceCount.textContent = serviceQueue.length;
            flowServiceName.textContent = service.name;
            var descriptionHtml = formatDescription(service.description || "");
            flowServiceDescription.innerHTML = descriptionHtml
                ? '<details class="flow-description-panel"><summary>ℹ️ See What\'s Included in this Service</summary><div class="flow-description__content">' + descriptionHtml + "</div></details>"
                : "";
            if (autoExpandDescription) {
                var detailsEl = flowServiceDescription.querySelector("details");
                if (detailsEl) {
                    detailsEl.setAttribute("open", "true");
                }
                autoExpandDescription = false;
            }

            var previousSelection = flowState.selections[service.id];
            flowOptionsContainer.innerHTML = "";

            var ensureSurveyButton = function () {
                if (!flowNextButton) return;
                if (!flowSurveyButton) {
                    flowSurveyButton = document.createElement("button");
                    flowSurveyButton.type = "button";
                    flowSurveyButton.className = "button button--danger";
                    flowSurveyButton.textContent = "Request Survey";
                    flowSurveyButton.style.display = "none";
                    flowNextButton.parentNode.insertBefore(flowSurveyButton, flowNextButton.nextSibling);
                }
            };

            var updateActionButtons = function (selection) {
                ensureSurveyButton();
                // In read-only mode, keep all action buttons hidden
                if (flowReadOnlyMode) {
                    if (flowNextButton) flowNextButton.style.display = "none";
                    if (flowSurveyButton) flowSurveyButton.style.display = "none";
                    return;
                }
                var isBlocker = selection && selection.payload && selection.payload.is_blocker;
                var showSurvey = isBlocker === true;
                if (flowNextButton) {
                    flowNextButton.style.display = showSurvey ? "none" : "inline-flex";
                    flowNextButton.disabled = !selection || showSurvey;
                }
                if (flowSurveyButton) {
                    flowSurveyButton.style.display = showSurvey ? "inline-flex" : "none";
                    flowSurveyButton.disabled = !showSurvey;
                    flowSurveyButton.setAttribute("data-service-id", service.id);
                }
            };

            var setPriceDisplay = function (selection) {
                if (!flowPriceDisplay) return;
                if (selection && selection.priceDisplay) {
                    flowPriceDisplay.textContent = selection.priceDisplay;
                    return;
                }
                if (selection && typeof selection.price === "number") {
                    flowPriceDisplay.textContent = formatPrice(selection.price);
                    return;
                }
                flowPriceDisplay.textContent = "Select an option to see pricing.";
            };

            var handleSelectionChange = function (selection) {
                if (selection) {
                        flowState.selections[service.id] = Object.assign({}, selection, { serviceId: service.id, serviceName: service.name, serviceCategory: service.service_category || "one_time" });
                } else {
                    delete flowState.selections[service.id];
                }
                persistFlowState();
                setPriceDisplay(selection);
                updateActionButtons(selection);
                updateMiniCart();
            };

            ensureSurveyButton();
            if (flowSurveyButton) {
                flowSurveyButton.onclick = function () {
                    var current = flowState.selections[service.id];
                    if (!current || !(current.payload && current.payload.is_blocker)) {
                        return;
                    }
                    current.price = null;
                    current.priceDisplay = current.priceDisplay || "Survey Needed";
                    current.payload.is_survey_request = true;
                    current.optionDetails = current.optionDetails || "Survey required";
                    flowState.selections[service.id] = current;
                    persistFlowState();
                    setPriceDisplay(current);
                    updateMiniCart();
                    showChoicePrompt();
                };
            }

            var pricingType = service.pricing_type || (service.tenancy_rates && service.tenancy_rates.length ? "tenancy" : service.pricing_tiers && service.pricing_tiers.length ? "deep" : service.pricing_items && service.pricing_items.length ? "itemized" : "options");
            if (pricingType === "survey") {
                renderSurveyConfigurator(service, handleSelectionChange);
            } else if (pricingType === "tenancy") {
                renderTenancyConfigurator(service, previousSelection, handleSelectionChange);
            } else if (pricingType === "airbnb") {
                renderAirbnbConfigurator(service, previousSelection, handleSelectionChange);
            } else if (pricingType === "deep") {
                renderDeepTierConfigurator(service, previousSelection, handleSelectionChange);
            } else if (pricingType === "itemized") {
                renderItemizedConfigurator(service, { items: service.pricing_items || [], discounts: [] }, previousSelection, handleSelectionChange);
            } else {
                renderLegacyOptions(service, previousSelection, handleSelectionChange);
            }

            setPriceDisplay(flowState.selections[service.id]);

            // Handle read-only mode (from "Read More" button without postcode)
            var readOnlyBanner = document.getElementById("flow-readonly-banner");
            if (flowReadOnlyMode) {
                // Show read-only banner if not exists
                if (!readOnlyBanner) {
                    readOnlyBanner = document.createElement("div");
                    readOnlyBanner.id = "flow-readonly-banner";
                    readOnlyBanner.className = "flow-readonly-banner";
                    readOnlyBanner.innerHTML = '<p>📍 <strong>Want to book this service?</strong> Enter your postcode to check availability and proceed with booking.</p><button type="button" class="button button--primary" id="flow-readonly-book-btn">Enter Postcode to Book</button>';
                    var stepContent = serviceModal.querySelector("[data-flow-step='1']");
                    if (stepContent) {
                        stepContent.insertBefore(readOnlyBanner, stepContent.firstChild);
                    }
                    // Bind the book button
                    var bookBtn = document.getElementById("flow-readonly-book-btn");
                    if (bookBtn) {
                        bookBtn.addEventListener("click", function () {
                            closeServiceModal();
                            var currentService = getServiceFromQueue(currentServiceIndex);
                            openPostcodeModal(currentService ? currentService.id : null, true);
                        });
                    }
                }
                readOnlyBanner.style.display = "block";
                // Hide action buttons in read-only mode
                if (flowNextButton) flowNextButton.style.display = "none";
                if (flowPrevButton) flowPrevButton.style.display = "none";
                if (flowSkipButton) flowSkipButton.style.display = "none";
                if (flowSurveyButton) flowSurveyButton.style.display = "none";
            } else {
                if (readOnlyBanner) readOnlyBanner.style.display = "none";
                if (flowPrevButton) {
                    flowPrevButton.style.display = "";
                    flowPrevButton.disabled = currentServiceIndex === 0;
                }

                if (flowNextButton) {
                    flowNextButton.style.display = "";
                    var isLast = currentServiceIndex === serviceQueue.length - 1;
                    flowNextButton.textContent = isLast ? "Review Summary" : "Next Service";
                    updateActionButtons(flowState.selections[service.id]);
                }
            }
        };

        var goToNextService = function () {
            if (currentServiceIndex < serviceQueue.length - 1) {
                currentServiceIndex += 1;
                renderServiceStep();
            } else {
                // Prevent advancing to summary if nothing has been selected
                var orderedSels = getOrderedSelections();
                var bookable = orderedSels.filter(function (s) { return !s.isDomestic; });
                var hasDomesticPlan = Boolean(flowState.domesticPlan || flowState.domesticConfig);
                if (!bookable.length && !hasDomesticPlan) {
                    if (flowChoicePrompt) {
                        flowChoicePrompt.style.display = 'none';
                    }
                    if (flowOptionsContainer) {
                        var warn = document.createElement('p');
                        warn.style.cssText = 'color:#b91c1c;text-align:center;padding:1rem;font-weight:600;';
                        warn.textContent = 'Please select at least one service before continuing.';
                        flowOptionsContainer.innerHTML = '';
                        flowOptionsContainer.appendChild(warn);
                    }
                    // Go back to the first service in the queue
                    currentServiceIndex = 0;
                    renderServiceStep();
                    return;
                }
                setActiveStep(2);
                renderSummary();
            }
        };

        var showChoicePrompt = function () {
            if (!flowChoicePrompt) {
                // Fallback if HTML not present – use the old auto-advance
                goToNextService();
                return;
            }

            // Build a summary of what the user selected so far
            var selections = getOrderedSelections();
            var hasDomestic = Boolean(flowState.domesticPlan);
            var hasRegularSelections = Object.keys(flowState.selections).length > 0;

            if (flowChoiceSelected) {
                flowChoiceSelected.innerHTML = "";
                if (selections.length) {
                    var heading = document.createElement("p");
                    heading.className = "flow-choice-prompt__heading";
                    heading.innerHTML = "<strong>\u2705 Your selections so far:</strong>";
                    flowChoiceSelected.appendChild(heading);
                    var ul = document.createElement("ul");
                    ul.className = "flow-choice-prompt__list";
                    selections.forEach(function (sel) {
                        var li = document.createElement("li");
                        li.innerHTML = "<strong>" + sel.serviceName + "</strong> \u2013 " + sel.optionLabel + " <span style=\"color:var(--primary-color);font-weight:600;\">(" + (sel.priceDisplay || formatPrice(sel.price)) + ")</span>";
                        ul.appendChild(li);
                    });
                    flowChoiceSelected.appendChild(ul);
                }
            }

            // Set contextual question text
            if (flowChoiceQuestion) {
                flowChoiceQuestion.textContent = "Would you like to add more services or continue to review & schedule?";
            }

            // If there's no domestic plan and this is the last service, skip prompt
            if (!hasDomestic && currentServiceIndex >= serviceQueue.length - 1) {
                setActiveStep(2);
                renderSummary();
                return;
            }

            // Show the prompt, hide step 1 content
            flowChoicePrompt.style.display = "block";
            if (flowOptionsContainer) flowOptionsContainer.style.display = "none";
            if (flowPriceDisplay) flowPriceDisplay.style.display = "none";
            var step1Actions = serviceModal.querySelector('[data-flow-step="1"] .flow-actions');
            if (step1Actions) step1Actions.style.display = "none";
            // Also hide the step header when in prompt mode
            var step1Header = serviceModal.querySelector('[data-flow-step="1"] .flow-step__header');
            if (step1Header) step1Header.style.display = "none";
        };

        var hideChoicePrompt = function () {
            if (flowChoicePrompt) {
                flowChoicePrompt.style.display = "none";
            }
            // Restore step 1 content visibility
            if (flowOptionsContainer) flowOptionsContainer.style.display = "";
            if (flowPriceDisplay) flowPriceDisplay.style.display = "";
            var step1Actions = serviceModal.querySelector('[data-flow-step="1"] .flow-actions');
            if (step1Actions) step1Actions.style.display = "";
            var step1Header = serviceModal.querySelector('[data-flow-step="1"] .flow-step__header');
            if (step1Header) step1Header.style.display = "";
        };

        /* ── Domestic Configurator Step ────────────────────────────── */
        var domesticStepEl = document.getElementById("flow-domestic-step");
        var domesticCfgPlanName = document.getElementById("domestic-cfg-plan-name");
        var domesticCfgLabel = document.getElementById("domestic-cfg-label");
        var domesticCfgRate = document.getElementById("domestic-cfg-rate");
        var domesticCfgCleaners = document.getElementById("domestic-cfg-cleaners");
        var domesticCleanersMinus = document.getElementById("domestic-cleaners-minus");
        var domesticCleanersPlus = document.getElementById("domestic-cleaners-plus");
        var domesticCfgHours = document.getElementById("domestic-cfg-hours");
        var domesticHoursMinus = document.getElementById("domestic-hours-minus");
        var domesticHoursPlus = document.getElementById("domestic-hours-plus");
        var domesticCfgEstRate = document.getElementById("domestic-cfg-est-rate");
        var domesticCfgEstHours = document.getElementById("domestic-cfg-est-hours");
        var domesticCfgEstTotal = document.getElementById("domestic-cfg-est-total");
        var domesticCfgCancel = document.getElementById("domestic-cfg-cancel");
        var domesticCfgContinue = document.getElementById("domestic-cfg-continue");

        var recalcDomesticEstimate = function () {
            if (!flowState.domesticConfig) return;
            var dc = flowState.domesticConfig;
            var rate = parseFloat(dc.price_per_hour) || 0;
            var cleaners = dc.cleaners || 1;
            var hours = dc.hours || 3;
            var total = rate * cleaners * hours;
            dc.total = total;

            if (domesticCfgEstRate) {
                domesticCfgEstRate.textContent = formatPrice(rate) + "/hr \u00D7 " + cleaners + " cleaner" + (cleaners > 1 ? "s" : "");
            }
            if (domesticCfgEstHours) {
                domesticCfgEstHours.textContent = hours + " hour" + (hours > 1 ? "s" : "");
            }
            if (domesticCfgEstTotal) {
                domesticCfgEstTotal.textContent = formatPrice(total);
            }
            if (domesticCfgCleaners) domesticCfgCleaners.value = cleaners;
            if (domesticCfgHours) domesticCfgHours.value = hours;

            persistFlowState();
            updateMiniCart();
        };

        var showDomesticStep = function () {
            if (!domesticStepEl || !flowState.domesticConfig) return;
            var dc = flowState.domesticConfig;
            var rate = parseFloat(dc.price_per_hour) || 0;

            // Populate badge
            if (domesticCfgPlanName) domesticCfgPlanName.textContent = dc.plan_name || "Domestic Cleaning";
            if (domesticCfgLabel) domesticCfgLabel.textContent = dc.plan_name || "Plan";
            if (domesticCfgRate) domesticCfgRate.textContent = formatPrice(rate) + "/hr per cleaner";

            // Set stepper values
            if (domesticCfgCleaners) domesticCfgCleaners.value = dc.cleaners || 1;
            if (domesticCfgHours) domesticCfgHours.value = dc.hours || 3;

            // Show domestic step, hide all regular flow steps
            var allSteps = serviceModal.querySelectorAll(".flow-step");
            allSteps.forEach(function (step) {
                step.style.display = "none";
                step.classList.remove("is-active");
            });
            domesticStepEl.style.display = "";
            domesticStepEl.classList.add("is-active");

            recalcDomesticEstimate();
        };

        var hideDomesticStep = function () {
            if (domesticStepEl) {
                domesticStepEl.style.display = "none";
                domesticStepEl.classList.remove("is-active");
            }
        };

        // Stepper button handlers
        if (domesticCleanersMinus) {
            domesticCleanersMinus.addEventListener("click", function () {
                if (!flowState.domesticConfig) return;
                var min = 1;
                if (flowState.domesticConfig.cleaners > min) {
                    flowState.domesticConfig.cleaners -= 1;
                    recalcDomesticEstimate();
                }
            });
        }
        if (domesticCleanersPlus) {
            domesticCleanersPlus.addEventListener("click", function () {
                if (!flowState.domesticConfig) return;
                var max = 10;
                if (flowState.domesticConfig.cleaners < max) {
                    flowState.domesticConfig.cleaners += 1;
                    recalcDomesticEstimate();
                }
            });
        }
        if (domesticHoursMinus) {
            domesticHoursMinus.addEventListener("click", function () {
                if (!flowState.domesticConfig) return;
                var min = 2;
                if (flowState.domesticConfig.hours > min) {
                    flowState.domesticConfig.hours -= 1;
                    recalcDomesticEstimate();
                }
            });
        }
        if (domesticHoursPlus) {
            domesticHoursPlus.addEventListener("click", function () {
                if (!flowState.domesticConfig) return;
                var max = 12;
                if (flowState.domesticConfig.hours < max) {
                    flowState.domesticConfig.hours += 1;
                    recalcDomesticEstimate();
                }
            });
        }

        // Browse More Services → show regular service flow from Service 1
        var domesticCfgBrowse = document.getElementById("domestic-cfg-browse");
        if (domesticCfgBrowse) {
            domesticCfgBrowse.addEventListener("click", function () {
                if (flowState.domesticConfig) {
                    recalcDomesticEstimate();
                    persistFlowState();
                }
                hideDomesticStep();
                currentServiceIndex = 0;
                serviceQueue = buildServiceQueue(null);
                setActiveStep(1);
                renderServiceStep();
            });
        }

        // Continue → go to summary (Step 2)
        if (domesticCfgContinue) {
            domesticCfgContinue.addEventListener("click", function () {
                if (!flowState.domesticConfig) return;
                recalcDomesticEstimate();
                persistFlowState();
                hideDomesticStep();
                setActiveStep(2);
                renderSummary();
            });
        }

        // Cancel → close modal
        if (domesticCfgCancel) {
            domesticCfgCancel.addEventListener("click", function () {
                flowState.domesticPlan = null;
                flowState.domesticConfig = null;
                window.__domesticPlanContext = null;
                persistFlowState();
                updateMiniCart();
                hideDomesticStep();
                closeServiceModal();
            });
        }
        /* ── End Domestic Configurator ─────────────────────────────── */

        var goToPreviousService = function () {
            if (currentServiceIndex === 0) {
                return;
            }
            currentServiceIndex -= 1;
            renderServiceStep();
        };

        var renderSummary = function () {
            if (!flowSummaryList || !flowSummaryTotal) {
                return;
            }
            var selections = getOrderedSelections();
            flowSummaryList.innerHTML = "";

            if (!selections.length) {
                var empty = document.createElement("li");
                empty.textContent = "No services selected.";
                flowSummaryList.appendChild(empty);
            } else {
                selections.forEach(function (selection) {
                    var item = document.createElement("li");
                    var info = document.createElement("div");
                    var title = document.createElement("strong");
                    title.textContent = selection.serviceName;
                    var detail = document.createElement("p");
                    detail.textContent = selection.optionLabel + (selection.optionDetails ? " • " + selection.optionDetails : "");
                    detail.style.margin = "0";
                    detail.style.fontSize = "0.9rem";
                    info.appendChild(title);
                    info.appendChild(detail);

                    var price = document.createElement("div");
                    price.className = "flow-option__price";
                    price.textContent = selection.priceDisplay || formatPrice(selection.price);

                    if (selection.isDomestic) {
                        // Domestic plan: show Edit button to reconfigure
                        var editDomesticBtn = document.createElement("button");
                        editDomesticBtn.type = "button";
                        editDomesticBtn.textContent = "Edit";
                        editDomesticBtn.className = "button button--outline button--small";
                        editDomesticBtn.addEventListener("click", function () {
                            showDomesticStep();
                        });
                        var removeDomesticBtn = document.createElement("button");
                        removeDomesticBtn.type = "button";
                        removeDomesticBtn.textContent = "Remove";
                        removeDomesticBtn.className = "button button--outline button--small";
                        removeDomesticBtn.style.marginLeft = "0.35rem";
                        removeDomesticBtn.addEventListener("click", function () {
                            flowState.domesticPlan = null;
                            flowState.domesticConfig = null;
                            window.__domesticPlanContext = null;
                            persistFlowState();
                            renderSummary();
                            updateMiniCart();
                        });
                        item.appendChild(info);
                        item.appendChild(price);
                        item.appendChild(editDomesticBtn);
                        item.appendChild(removeDomesticBtn);
                    } else {
                        var editButton = document.createElement("button");
                        editButton.type = "button";
                        editButton.textContent = "Edit";
                        editButton.setAttribute("data-edit-service", selection.serviceId);
                        item.appendChild(info);
                        item.appendChild(price);
                        item.appendChild(editButton);
                    }

                    flowSummaryList.appendChild(item);
                });
            }

            var total = selections.reduce(function (sum, selection) {
                if (typeof selection.price === "number" && !Number.isNaN(selection.price)) {
                    return sum + selection.price;
                }
                return sum;
            }, 0);
            var hasCustom = selections.some(function (selection) { return typeof selection.price !== "number" || Number.isNaN(selection.price); });
            var hasSurvey = selections.some(function (selection) { return selection.payload && selection.payload.is_survey_request; });

            // Show discount breakdown rows if any selection has a bulk/item discount
            var totalDiscount = selections.reduce(function (sum, sel) {
                return sum + (sel.payload && typeof sel.payload.discount_amount === "number" ? sel.payload.discount_amount : 0);
            }, 0);
            var totalSubtotalBeforeDiscount = selections.reduce(function (sum, sel) {
                return sum + (sel.payload && typeof sel.payload.subtotal === "number" ? sel.payload.subtotal : (typeof sel.price === "number" ? sel.price : 0));
            }, 0);
            var discountRowEl = document.getElementById("flow-summary-discount-row");
            var discountAmtEl = document.getElementById("flow-summary-discount-amount");
            var discountLabelEl = document.getElementById("flow-summary-discount-label");
            var subtotalRowEl = document.getElementById("flow-summary-subtotal-row");
            var subtotalAmtEl = document.getElementById("flow-summary-services-subtotal");
            if (totalDiscount > 0 && discountRowEl && discountAmtEl && subtotalRowEl && subtotalAmtEl) {
                var discountPercent = selections.reduce(function (p, sel) {
                    return p || (sel.payload && sel.payload.discount_percent) || 0;
                }, 0);
                subtotalRowEl.style.display = "flex";
                subtotalAmtEl.textContent = formatPrice(totalSubtotalBeforeDiscount);
                discountRowEl.style.display = "flex";
                discountAmtEl.textContent = "-" + formatPrice(totalDiscount);
                if (discountLabelEl && discountPercent) {
                    discountLabelEl.textContent = discountPercent + "% Bulk Discount";
                }
            } else {
                if (subtotalRowEl) subtotalRowEl.style.display = "none";
                if (discountRowEl) discountRowEl.style.display = "none";
            }

            setSubmitButtonLabel(hasSurvey);
            updateSummaryTotals(total, hasCustom, "", hasSurvey);
            if (askForPostcode) {
                lastSummaryTotals.hasSurvey = hasSurvey;
                if (!hasSurvey) {
                    var summaryPostcode = flowState.customer.postcode || (flowSummaryPostcodeInput && flowSummaryPostcodeInput.value) || "";
                    summaryTravelNeedsRefresh = shouldRefreshTravelQuote(summaryPostcode);
                    summaryBlockMessageOverride = summaryTravelNeedsRefresh && summaryPostcode ? "Click Update travel to refresh pricing." : "";
                } else {
                    setStoredTravelQuote(null);
                    flowState.travelQuote = null;
                    updateTravelSnapshot("");
                    summaryTravelNeedsRefresh = false;
                    summaryBlockMessageOverride = "";
                    setSummaryFeedbackMessage("", null, false);
                }
                updateSummaryNextState();
            }
        };

        var buildServiceQueue = function (startServiceId) {
            // Build a reordered queue: selected service first, then remaining services
            if (!SERVICE_CATALOG.length) return [];
            var startIndex = SERVICE_CATALOG.findIndex(function (service) { return String(service.id) === String(startServiceId); });
            if (startIndex < 0) startIndex = 0;

            var queue = [];
            // Add the selected service first
            queue.push(SERVICE_CATALOG[startIndex].id);
            
            // Add services that come AFTER the selected one
            for (var i = startIndex + 1; i < SERVICE_CATALOG.length; i++) {
                queue.push(SERVICE_CATALOG[i].id);
            }
            
            // Add services that come BEFORE the selected one (so user can add them too)
            for (var j = 0; j < startIndex; j++) {
                queue.push(SERVICE_CATALOG[j].id);
            }
            
            return queue;
        };

        var getServiceFromQueue = function (queueIndex) {
            if (queueIndex < 0 || queueIndex >= serviceQueue.length) return null;
            var serviceId = serviceQueue[queueIndex];
            return SERVICE_CATALOG.find(function (s) { return String(s.id) === String(serviceId); });
        };

        var openServiceModal = function (serviceId, autoExpandDetails, readOnly) {
            // Build reordered queue starting with the selected service
            serviceQueue = buildServiceQueue(serviceId);
            if (!serviceQueue.length) return; // catalog not loaded yet — applyCatalogUpdate will retry

            // Clear stale selections from other services so a one-time service
            // never inherits contract form state from a previous contract service session.
            if (serviceId) {
                Object.keys(flowState.selections || {}).forEach(function (key) {
                    if (String(key) !== String(serviceId)) {
                        delete flowState.selections[key];
                    }
                });
                // Also reset contract fields so they don't bleed over
                if (flowState.contract) {
                    flowState.contract.signer_name = '';
                    flowState.contract.agreed = false;
                }
                if (flowState.schedule) {
                    flowState.schedule.contract_frequency = '';
                }
                persistFlowState();
            }
            currentServiceIndex = 0;
            autoExpandDescription = Boolean(autoExpandDetails);
            flowReadOnlyMode = Boolean(readOnly);
            updateMiniCart();
            hydrateContactFields();
            serviceModal.classList.add("is-open");
            serviceModal.setAttribute("aria-hidden", "false");
            document.body.style.overflow = "hidden";
            sendAnalyticsEvent("service_view", { context: "modal", service: serviceId });

            // If entering from a domestic CTA, show the domestic configurator
            if (pendingDomesticEntry && flowState.domesticPlan) {
                pendingDomesticEntry = false;
                showDomesticStep();
            } else {
                hideDomesticStep();
                setActiveStep(1);
                renderServiceStep();
            }
        };

        var closeServiceModal = function () {
            serviceModal.classList.remove("is-open");
            serviceModal.setAttribute("aria-hidden", "true");
            document.body.style.overflow = "";
            flowReadOnlyMode = false; // Reset read-only mode when closing
        };

        var openPostcodeModal = function (serviceId, autoExpandDetails) {
            if (!askForPostcode) {
                openServiceModal(serviceId, autoExpandDetails);
                return;
            }
            pendingServiceId = serviceId || null;
            pendingAutoExpand = Boolean(autoExpandDetails);
            if (postcodeInput) {
                postcodeInput.value = (flowState.customer.postcode || getStoredPostcode() || "").trim();
            }
            if (postcodeFeedback) {
                postcodeFeedback.textContent = "";
                postcodeFeedback.className = "form-feedback";
            }
            if (postcodeModal) {
                postcodeModal.classList.add("is-open");
                postcodeModal.setAttribute("aria-hidden", "false");
                document.body.style.overflow = "hidden";
                if (postcodeInput) {
                    postcodeInput.focus();
                }
            }
        };

        var closePostcodeModal = function () {
            if (!postcodeModal) {
                return;
            }
            postcodeModal.classList.remove("is-open");
            postcodeModal.setAttribute("aria-hidden", "true");
            document.body.style.overflow = "";
        };

        var openCoverageModal = function () {
            if (serviceModal) {
                serviceModal.classList.remove("is-open");
                serviceModal.setAttribute("aria-hidden", "true");
            }
            closePostcodeModal();
            if (coverageModal) {
                coverageModal.classList.add("is-open");
                coverageModal.setAttribute("aria-hidden", "false");
                document.body.style.overflow = "hidden";
            }
        };

        var closeCoverageModal = function () {
            if (!coverageModal) {
                return;
            }
            coverageModal.classList.remove("is-open");
            coverageModal.setAttribute("aria-hidden", "true");
            document.body.style.overflow = "";
        };

        var openExtendedCoverageModal = function (quote) {
            pendingExtendedCoverageQuote = quote;
            var travelFee = quote && typeof quote.travel_fee === "number" ? quote.travel_fee : 0;
            var withFeeSection = document.getElementById("extended-coverage-with-fee");
            var notAvailableSection = document.getElementById("extended-coverage-not-available");
            var modalTitle = document.getElementById("extended-coverage-modal-title");
            
            if (travelFee === 0) {
                // No fee means we're not servicing this area
                if (withFeeSection) withFeeSection.style.display = "none";
                if (notAvailableSection) notAvailableSection.style.display = "block";
                if (modalTitle) modalTitle.textContent = "Outside Service Area";
            } else {
                // Has a fee - show normal extended coverage options
                if (withFeeSection) withFeeSection.style.display = "block";
                if (notAvailableSection) notAvailableSection.style.display = "none";
                if (modalTitle) modalTitle.textContent = "Extended Coverage Area";
                if (extendedCoverageFee) {
                    extendedCoverageFee.textContent = formatPrice(travelFee);
                }
            }
            
            if (extendedCoverageModal) {
                extendedCoverageModal.classList.add("is-open");
                extendedCoverageModal.setAttribute("aria-hidden", "false");
                document.body.style.overflow = "hidden";
            }
        };

        var closeExtendedCoverageModal = function (accepted) {
            if (!extendedCoverageModal) {
                return;
            }
            
            // Move focus away from modal before hiding to avoid aria-hidden warning
            if (document.activeElement && extendedCoverageModal.contains(document.activeElement)) {
                document.activeElement.blur();
            }
            
            extendedCoverageModal.classList.remove("is-open");
            extendedCoverageModal.setAttribute("aria-hidden", "true");
            document.body.style.overflow = "";
            
            if (accepted && pendingExtendedCoverageQuote) {
                // User accepted extended coverage - proceed with the quote
                flowState.travelQuote = pendingExtendedCoverageQuote;
                flowState.customer.postcode = pendingExtendedCoverageQuote.customer_postcode || flowState.customer.postcode;
                flowState.extendedCoverageAccepted = true;
                updateTravelSnapshot(flowState.customer.postcode);
                persistFlowState();
                setStoredTravelQuote(pendingExtendedCoverageQuote, flowState.customer.postcode);
                
                // Update totals with the new quote
                var selections = getOrderedSelections();
                var serviceTotal = selections.reduce(function (sum, sel) {
                    return typeof sel.price === "number" ? sum + sel.price : sum;
                }, 0);
                var hasCustom = selections.some(function (sel) { return sel.isCustom; });
                updateSummaryTotals(serviceTotal, hasCustom, "", lastSummaryTotals.hasSurvey || false);
                summaryTravelNeedsRefresh = false;
                summaryBlockMessageOverride = "";
                setSummaryFeedbackMessage("Extended coverage confirmed.", "success", false);
                updateSummaryNextState();
                
                // Now open the service modal if not already open
                if (pendingServiceId && !serviceModal.classList.contains("is-open")) {
                    continueToServiceModal();
                }
            } else {
                // User cancelled - clear the quote
                pendingExtendedCoverageQuote = null;
                flowState.travelQuote = null;
                flowState.extendedCoverageAccepted = false;
                setStoredTravelQuote(null);
                updateTravelSnapshot("");
                persistFlowState();
                summaryTravelNeedsRefresh = true;
                summaryBlockMessageOverride = "Confirm extended coverage to continue.";
                setSummaryFeedbackMessage("", null, false);
                updateSummaryNextState();
            }
            pendingExtendedCoverageQuote = null;
        };

        // Set up extended coverage modal buttons
        if (extendedCoverageProceed) {
            extendedCoverageProceed.addEventListener("click", function () {
                closeExtendedCoverageModal(true);
            });
        }
        if (extendedCoverageCancel) {
            extendedCoverageCancel.addEventListener("click", function () {
                closeExtendedCoverageModal(false);
            });
        }
        if (extendedCoverageClose) {
            extendedCoverageClose.addEventListener("click", function () {
                closeExtendedCoverageModal(false);
            });
        }
        
        // Close button for "not available" section
        var extendedCoverageCloseBtn = document.getElementById("extended-coverage-close-btn");
        if (extendedCoverageCloseBtn) {
            extendedCoverageCloseBtn.addEventListener("click", function () {
                closeExtendedCoverageModal(false);
            });
        }
        
        // Contact link - close modal and scroll to contact
        var extendedCoverageContact = document.getElementById("extended-coverage-contact");
        if (extendedCoverageContact) {
            extendedCoverageContact.addEventListener("click", function () {
                closeExtendedCoverageModal(false);
            });
        }

        var resetFlow = function () {
            flowState = createDefaultFlowState();
            window.__domesticPlanContext = null;
            pendingDomesticEntry = false;
            hideDomesticStep();
            setStoredTravelQuote(null);
            updateTravelSnapshot("");
            travelQuotePending = false;
            lastTravelQuotePostcode = "";
            summaryTravelNeedsRefresh = false;
            summaryBlockMessageOverride = "";
            summaryInstructionLocked = false;
            setSummaryFeedbackMessage("", null, false);
            persistFlowState();
            // Rebuild queue from original catalog order
            serviceQueue = SERVICE_CATALOG.map(function (s) { return s.id; });
            currentServiceIndex = 0;
            setActiveStep(1);
            renderServiceStep();
            updateMiniCart();
            hydrateContactFields();
            syncPaymentOptionInputs();
            setSubmitButtonLabel(false);
            updateSummaryNextState();
            if (flowFeedback) {
                flowFeedback.textContent = "";
                flowFeedback.classList.remove("is-error", "is-success");
            }
        };

        if (flowPrevButton) {
            flowPrevButton.addEventListener("click", function () {
                goToPreviousService();
            });
        }

        if (flowNextButton) {
            flowNextButton.addEventListener("click", function () {
                var activeService = getServiceFromQueue(currentServiceIndex);
                if (!activeService) {
                    flowPriceDisplay.textContent = "Services will load shortly.";
                    return;
                }
                if (!flowState.selections[activeService.id]) {
                    flowPriceDisplay.textContent = "Please select an option or skip this service.";
                    return;
                }
                // Show choice prompt instead of auto-advancing
                showChoicePrompt();
            });
        }

        // Choice prompt buttons
        if (flowChoiceBrowse) {
            flowChoiceBrowse.addEventListener("click", function () {
                hideChoicePrompt();
                // Advance to the next service in the queue (not back to 0)
                currentServiceIndex = Math.min(currentServiceIndex + 1, serviceQueue.length - 1);
                setActiveStep(1);
                renderServiceStep();
            });
        }

        if (flowChoiceContinue) {
            flowChoiceContinue.addEventListener("click", function () {
                hideChoicePrompt();
                setActiveStep(2);
                renderSummary();
            });
        }

        if (flowSkipButton) {
            flowSkipButton.addEventListener("click", function () {
                var activeService = getServiceFromQueue(currentServiceIndex);
                if (!activeService) {
                    return;
                }
                delete flowState.selections[activeService.id];
                persistFlowState();
                renderServiceStep();
                updateMiniCart();
                goToNextService();
            });
        }

        if (flowSummaryBackButton) {
            flowSummaryBackButton.addEventListener("click", function () {
                // If there's only a domestic config and no regular selections, go back to domestic step
                if (flowState.domesticConfig && Object.keys(flowState.selections).length === 0) {
                    showDomesticStep();
                } else {
                    setActiveStep(1);
                    renderServiceStep();
                }
            });
        }

        if (flowSummaryNextButton) {
            flowSummaryNextButton.addEventListener("click", function () {
                updateSummaryNextState();
                if (flowSummaryNextButton.disabled) {
                    if (flowSummaryPostcodeInput) {
                        flowSummaryPostcodeInput.focus();
                    }
                    return;
                }
                setSummaryFeedbackMessage("", null, false);
                setActiveStep(3);
                hydrateContactFields();
            });
        }

        if (flowScheduleBackButton) {
            flowScheduleBackButton.addEventListener("click", function () {
                setActiveStep(2);
                renderSummary();
            });
        }

        if (flowSummaryList) {
            flowSummaryList.addEventListener("click", function (event) {
                var target = event.target.closest("[data-edit-service]");
                if (!target) {
                    return;
                }
                var serviceId = target.getAttribute("data-edit-service");
                var queueIndex = serviceQueue.findIndex(function (id) { return String(id) === String(serviceId); });
                if (queueIndex >= 0) {
                    currentServiceIndex = queueIndex;
                    setActiveStep(1);
                    renderServiceStep();
                }
            });
        }

        var handleFieldChange = function (input, updater) {
            if (!input) {
                return;
            }
            input.addEventListener("input", function () {
                updater(this.value);
                persistFlowState();
            });
        };

        handleFieldChange(flowNameInput, function (value) { flowState.customer.name = value; });
        handleFieldChange(flowEmailInput, function (value) { flowState.customer.email = value; });
        handleFieldChange(flowPhoneInput, function (value) { flowState.customer.phone = value; });
        handleFieldChange(flowLocationInput, function (value) { flowState.customer.location = value; });
        handleFieldChange(flowPostcodeInput, function (value) { flowState.customer.postcode = value; });
        if (flowSummaryPostcodeInput) {
            flowSummaryPostcodeInput.addEventListener("input", function () {
                var value = this.value || "";
                flowState.customer.postcode = value;
                if (askForPostcode) {
                    flowState.customer.location = value;
                }

                var normalized = normalizePostcodeValue(value);
                var snapshot = normalizePostcodeValue(flowState.travelPostcodeSnapshot || "");
                var quoteCleared = false;
                summaryBlockMessageOverride = "";
                setSummaryFeedbackMessage("", null, false);

                if (!normalized) {
                    if (flowState.travelQuote) {
                        flowState.travelQuote = null;
                        flowState.extendedCoverageAccepted = false;
                        quoteCleared = true;
                    }
                    setStoredTravelQuote(null);
                    updateTravelSnapshot("");
                    summaryTravelNeedsRefresh = false;
                    lastTravelQuotePostcode = "";
                } else if (normalized !== snapshot) {
                    flowState.travelQuote = null;
                    flowState.extendedCoverageAccepted = false;
                    setStoredTravelQuote(null);
                    updateTravelSnapshot("");
                    summaryTravelNeedsRefresh = true;
                    summaryBlockMessageOverride = "Address changed. Click Update travel to refresh.";
                    quoteCleared = true;
                    lastTravelQuotePostcode = "";
                } else {
                    summaryTravelNeedsRefresh = false;
                }

                if (quoteCleared) {
                    updateSummaryTotals(lastSummaryTotals.serviceTotal, lastSummaryTotals.hasCustom, "", lastSummaryTotals.hasSurvey || false);
                }

                persistFlowState();
                updateSummaryNextState();
            });
        }
        handleFieldChange(flowDateInput, function (value) { flowState.schedule.preferred_date = value; });
        handleFieldChange(flowTimeInput, function (value) { flowState.schedule.preferred_time = value; });
        handleFieldChange(flowContractFrequencyInput, function (value) {
            flowState.schedule.contract_frequency = value;
            // Re-evaluate signer/terms required state when frequency changes (relevant for hybrid)
            updateContractFrequencyVisibility();
        });
        handleFieldChange(flowContractSignerInput, function (value) { flowState.contract.signer_name = value; });
        handleFieldChange(flowNotesInput, function (value) { flowState.notes = value; });
        if (flowContractTermsInput) {
            flowContractTermsInput.addEventListener("change", function () {
                flowState.contract.agreed = Boolean(flowContractTermsInput.checked);
                persistFlowState();
            });
        }

        if (flowPostcodeInput) {
            flowPostcodeInput.addEventListener("change", function () {
                requestTravelQuoteIfChanged(this.value);
            });
        }
        if (flowRefreshTravelButton) {
            flowRefreshTravelButton.addEventListener("click", function () {
                refreshTravelQuote(lastSummaryTotals.serviceTotal, lastSummaryTotals.hasCustom);
            });
        }

        var continueToServiceModal = function () {
            closePostcodeModal();
            var target = pendingServiceId || (SERVICE_CATALOG[0] && SERVICE_CATALOG[0].id) || null;
            var expand = pendingAutoExpand;
            pendingServiceId = null;
            pendingAutoExpand = false;
            openServiceModal(target, expand);
        };

        if (postcodeForm && askForPostcode) {
            postcodeForm.addEventListener("submit", async function (event) {
                event.preventDefault();
                var postcodeValue = postcodeInput ? postcodeInput.value.trim() : "";

                if (postcodeFeedback) {
                    postcodeFeedback.textContent = "";
                    postcodeFeedback.className = "form-feedback";
                }

                if (!postcodeValue) {
                    if (postcodeFeedback) {
                        postcodeFeedback.textContent = "Please enter your postcode or address.";
                        postcodeFeedback.classList.add("is-error");
                    }
                    if (postcodeInput) {
                        postcodeInput.focus();
                    }
                    return;
                }

                if (postcodeSubmitButton) {
                    postcodeSubmitButton.disabled = true;
                    postcodeSubmitButton.textContent = "Loading...";
                }

                try {
                    var result = await fetchTravelQuote(postcodeValue, lastSummaryTotals.serviceTotal);
                    
                    // Check if extended coverage confirmation is needed
                    if (result && result.requiresExtendedConfirmation) {
                        closePostcodeModal();
                        openExtendedCoverageModal(result.quote);
                        return;
                    }
                    
                    hydrateContactFields();
                    updateSummaryTotals(lastSummaryTotals.serviceTotal, lastSummaryTotals.hasCustom, "", lastSummaryTotals.hasSurvey || false);
                    continueToServiceModal();
                } catch (error) {
                    if (postcodeFeedback) {
                        postcodeFeedback.textContent = error.message || "Unable to calculate travel.";
                        postcodeFeedback.classList.add("is-error");
                    }
                } finally {
                    if (postcodeSubmitButton) {
                        postcodeSubmitButton.disabled = false;
                        postcodeSubmitButton.textContent = "Continue";
                    }
                }
            });
        }

        if (postcodeModal) {
            postcodeModal.querySelectorAll("[data-close-modal]").forEach(function (element) {
                element.addEventListener("click", function () {
                    closePostcodeModal();
                });
            });
        }

        if (coverageModal) {
            coverageModal.querySelectorAll("[data-close-modal]").forEach(function (element) {
                element.addEventListener("click", function () {
                    closeCoverageModal();
                });
            });
        }

        if (editLocationButton && askForPostcode) {
            editLocationButton.addEventListener("click", function () {
                closeServiceModal();
                var currentService = getServiceFromQueue(currentServiceIndex);
                var target = currentService ? currentService.id : null;
                openPostcodeModal(target);
            });
        }

        serviceFlowForm.addEventListener("submit", async function (event) {
            event.preventDefault();
            console.log("Form submit handler fired, activeStep:", activeStep, "submissionPending:", submissionPending);
            
            // Check submission lock FIRST - prevent ANY duplicate submissions
            if (submissionPending) {
                console.log("Submission already in progress, ignoring duplicate");
                return;
            }
            
            // If not on step 3, exit
            if (activeStep !== 3) {
                console.log("Not on step 3, exiting");
                return;
            }
            
            // Set submission lock and disable button IMMEDIATELY
            submissionPending = true;
            var originalLabel = defaultSubmitLabel;
            if (flowSubmitButton) {
                flowSubmitButton.disabled = true;
                flowSubmitButton.textContent = "Submitting...";
            }
            console.log("Lock set, button disabled, proceeding with submit");

            try {
                // Sync latest field values into state before validation
                if (flowLocationInput) flowState.customer.location = (flowLocationInput.value || "").trim();
                if (flowPostcodeInput) flowState.customer.postcode = (flowPostcodeInput.value || "").trim();
                if (flowSummaryPostcodeInput && !flowState.customer.postcode) {
                    flowState.customer.postcode = (flowSummaryPostcodeInput.value || "").trim();
                }
                if (flowNameInput) flowState.customer.name = (flowNameInput.value || "").trim();
                if (flowEmailInput) flowState.customer.email = (flowEmailInput.value || "").trim();
                if (flowPhoneInput) flowState.customer.phone = (flowPhoneInput.value || "").trim();
                if (flowContractSignerInput) flowState.contract.signer_name = (flowContractSignerInput.value || "").trim();
                if (!flowState.contract.service_day) {
                    flowState.contract.service_day = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][new Date().getDay()];
                }
                if (flowContractTermsInput) flowState.contract.agreed = Boolean(flowContractTermsInput.checked);
                if (flowNotesInput) flowState.notes = (flowNotesInput.value || "").trim();
                if (flowPaymentOptionInputs && flowPaymentOptionInputs.length) {
                    var selectedPaymentInput = Array.prototype.find.call(flowPaymentOptionInputs, function (input) { return input.checked; });
                    flowState.payment_option = normalizePaymentOptionValue(selectedPaymentInput ? selectedPaymentInput.value : flowState.payment_option);
                }
                persistFlowState();

                // If postcode changed and we haven't already calculated for this postcode, refresh travel pricing
                // BUT skip if the quote is already calculated for this postcode (use cached result)
                if (askForPostcode) {
                    var currentPostcode = normalizePostcodeValue(flowState.customer.postcode || "");
                    var alreadyCalculated = Boolean(currentPostcode && lastTravelQuotePostcode === currentPostcode && flowState.travelQuote);
                    
                    if (currentPostcode && !alreadyCalculated && !travelQuotePending) {
                        try {
                            setSummaryFeedbackMessage("Updating travel pricing...", null, false);
                            await refreshTravelQuote(lastSummaryTotals.serviceTotal, lastSummaryTotals.hasCustom, true);
                        } catch (err) {
                            if (flowFeedback) {
                                flowFeedback.textContent = err.message || "Unable to calculate travel.";
                                flowFeedback.classList.add("is-error");
                            }
                            if (flowSubmitButton) {
                                flowSubmitButton.disabled = false;
                                flowSubmitButton.textContent = originalLabel;
                            }
                            submissionPending = false;
                            return;
                        }
                    }
                }

                var errors = [];
                if (!flowState.customer.name) errors.push("Please enter your full name.");
                if (!flowState.customer.email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(flowState.customer.email)) errors.push("Enter a valid email address.");
                if (!flowState.customer.phone || flowState.customer.phone.replace(/[^0-9]/g, "").length < 10) errors.push("Enter a valid UK phone number (at least 10 digits).");
                if (!flowState.customer.location) errors.push("Add your address or location.");
                if (askForPostcode && !flowState.customer.postcode) errors.push("Please add your postcode or address.");
                if (!flowState.schedule.preferred_date) errors.push("Choose a preferred date.");
                if (!flowState.schedule.preferred_time) errors.push("Choose a preferred time.");
                if (hasContractSelections() && !flowState.schedule.contract_frequency) errors.push("Choose a contract frequency.");
                if (hasContractSelections() && !flowState.contract.service_day) errors.push("Choose your preferred service day.");

                flowFeedback.classList.remove("is-error", "is-success");

                if (errors.length) {
                    flowFeedback.textContent = errors[0];
                    flowFeedback.classList.add("is-error");
                    if (flowSubmitButton) {
                        flowSubmitButton.disabled = false;
                        flowSubmitButton.textContent = originalLabel;
                    }
                    submissionPending = false;
                    return;
                }

                // Contract modal — shown before payload is built when a contract service is selected
                if (hasContractSelections() && typeof window.openContractModal === 'function') {
                    var contractResult = await window.openContractModal();
                    if (!contractResult) {
                        // User cancelled
                        if (flowSubmitButton) { flowSubmitButton.disabled = false; flowSubmitButton.textContent = originalLabel; }
                        submissionPending = false;
                        return;
                    }
                    flowState.contract.signer_name = contractResult.signer_name || '';
                    flowState.contract.agreed = true;
                    flowState.contract.contract_text = contractResult.contract_text || '';
                    persistFlowState();
                }

                var selections = getOrderedSelections();
                var bookableSelections = selections.filter(function (selection) {
                    return !selection.isDomestic;
                });
                var hasDomesticOnly = selections.length > 0 && bookableSelections.length === 0;
                if (!bookableSelections.length) {
                    flowFeedback.textContent = hasDomesticOnly
                        ? "Your domestic cleaning plan is managed separately. Please use the domestic booking section."
                        : "Please select at least one service option.";
                    flowFeedback.classList.add("is-error");
                    if (flowSubmitButton) {
                        flowSubmitButton.disabled = false;
                        flowSubmitButton.textContent = originalLabel;
                    }
                    submissionPending = false;
                    return;
                }

                var serviceSummary = bookableSelections.map(function (selection) {
                    return selection.serviceName + " – " + selection.optionLabel;
                }).join(", ") || "Custom package";

                console.log("Building payload for submission...");
                console.log("Current flowState.payment_option:", flowState.payment_option);
                var normalizedPaymentOption = normalizePaymentOptionValue(flowState.payment_option);
                console.log("Normalized payment_option for payload:", normalizedPaymentOption);
                
                var payload = {
                    source: "service-flow",
                    context_page: window.location.pathname,
                    notes: flowState.notes,
                    payment_option: normalizedPaymentOption,
                    customer: {
                        name: flowState.customer.name,
                        email: flowState.customer.email,
                        phone: flowState.customer.phone,
                        address: flowState.customer.location,
                        postcode: flowState.customer.postcode
                    },
                    schedule: {
                        preferred_date: flowState.schedule.preferred_date,
                        preferred_time: flowState.schedule.preferred_time,
                        contract_frequency: hasContractSelections(bookableSelections) ? flowState.schedule.contract_frequency : ""
                    },
                    contract_agreement: {
                        signer_name: hasContractSelections(bookableSelections) ? flowState.contract.signer_name : "",
                        service_day: hasContractSelections(bookableSelections) ? flowState.contract.service_day : "",
                        agreed: hasContractSelections(bookableSelections) ? Boolean(flowState.contract.agreed) : false,
                        contract_text: hasContractSelections(bookableSelections) ? (flowState.contract.contract_text || '') : ''
                    },
                    postcode: flowState.customer.postcode,
                    selections: bookableSelections.map(function (selection) {
                        return {
                            service_id: selection.serviceId,
                            service_option_id: selection.optionId,
                            option_label: selection.optionLabel,
                            price: selection.price,
                            option_details: selection.optionDetails || "",
                            pricing_model: selection.modelType || null,
                            pricing_payload: selection.payload || null
                        };
                    })
                };

                // Attach domestic plan context if the user came from a domestic pricing CTA
                var domesticCtx = flowState.domesticConfig || flowState.domesticPlan || window.__domesticPlanContext || null;
                if (domesticCtx) {
                    payload.domestic_plan = domesticCtx;
                    // Context cleared only on success below, not here, so re-submission retains it
                }

                console.log("Starting checkout/direct processing...", payload);
                var response = await fetch(apiBase + "/api/payments/start-checkout", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify(payload)
                });
                var data = await response.json().catch(function () { return {}; });
                
                console.log("Response from /start-checkout:", response.status, data);
                console.log("Response mode:", data.mode, "checkout_url:", data.checkout_url);

                if (response.ok) {
                    if (data.mode === "checkout" && data.checkout_url) {
                        var checkoutTotal = typeof data.amount_total === "number" && !Number.isNaN(data.amount_total)
                            ? " (" + formatPrice(data.amount_total) + ")"
                            : "";
                        flowFeedback.textContent = "Redirecting to secure payment" + checkoutTotal + "...";
                        flowFeedback.classList.add("is-success");
                        window.location.href = data.checkout_url;
                        return;
                    }

                    var refId = data.service_request_id || data.request_id || null;
                    var successMsg = (data.message || "Request received! We will confirm shortly.");
                    if (refId) successMsg += " Your reference: #" + refId;
                    flowFeedback.textContent = successMsg;
                    flowFeedback.classList.add("is-success");
                    sendAnalyticsEvent("request_submission", { form: "service-flow", request_id: data.request_id });
                    // Clear domestic context only on success
                    window.__domesticPlanContext = null;
                    resetFlow();
                    submissionPending = false;
                    window.setTimeout(closeServiceModal, 3000);
                } else {
                    flowFeedback.textContent = data.error || "Unable to submit right now. Please retry.";
                    flowFeedback.classList.add("is-error");
                    if (flowSubmitButton) {
                        flowSubmitButton.disabled = false;
                        flowSubmitButton.textContent = originalLabel;
                    }
                    submissionPending = false;
                }
            } catch (error) {
                console.error("Service flow submission failed", error);
                flowFeedback.textContent = "Unable to submit right now. Please retry.";
                flowFeedback.classList.add("is-error");
                if (flowSubmitButton) {
                    flowSubmitButton.disabled = false;
                    flowSubmitButton.textContent = originalLabel;
                }
                submissionPending = false;
            }
        });

        var bindServiceButtons = function () {
            document.querySelectorAll(".service-request-trigger, .service-read-more").forEach(function (button) {
                button.addEventListener("click", function (event) {
                    event.preventDefault();
                    var targetServiceId = button.getAttribute("data-service-id");
                    var autoExpand = button.getAttribute("data-auto-expand") === "true";
                    var isReadMore = button.classList.contains("service-read-more");
                    
                    // Read More should always show content without requiring postcode
                    if (isReadMore) {
                        openServiceModal(targetServiceId, autoExpand, true); // true = readOnly mode
                    } else if (askForPostcode) {
                        openPostcodeModal(targetServiceId, autoExpand);
                    } else {
                        openServiceModal(targetServiceId, autoExpand);
                    }
                });
            });
        };

        var applyCatalogUpdate = function (catalog) {
            SERVICE_CATALOG = Array.isArray(catalog) ? catalog : [];
            renderServiceCards();
            bindServiceButtons();
            if (SERVICE_CATALOG.length) {
                // Initialize queue with default catalog order
                serviceQueue = SERVICE_CATALOG.map(function (s) { return s.id; });
                currentServiceIndex = 0;
                setActiveStep(1);
                renderServiceStep();
                updateMiniCart();
                // Re-fire contract prefill if we arrived from a service detail page
                // and the modal hasn't opened yet (catalog was empty when the timeout fired)
                try {
                    var params = new URLSearchParams(window.location.search || "");
                    var pendingSvcId = Number(params.get("service_id") || 0);
                    if (pendingSvcId && !serviceModal.classList.contains("is-open")) {
                        var freq = String(params.get("contract_frequency") || "").trim().toLowerCase();
                        var sDay = String(params.get("service_day") || "").trim();
                        if (["weekly","fortnightly","monthly"].indexOf(freq) !== -1) flowState.schedule.contract_frequency = freq;
                        if (["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"].indexOf(sDay) !== -1) flowState.contract.service_day = sDay;
                        persistFlowState();
                        if (askForPostcode && typeof window.openPostcodeModal === "function") {
                            window.openPostcodeModal(pendingSvcId);
                        } else {
                            openServiceModal(pendingSvcId);
                        }
                    }
                } catch (e) { /* ignore */ }
            }
        };

        var fetchCatalogFromApi = async function () {
            try {
                var response = await fetch(apiBase + "/api/services");
                if (!response.ok) {
                    return;
                }
                var data = await response.json().catch(function () { return []; });
                var normalized = (Array.isArray(data) ? data : []).map(normalizeService).filter(Boolean);
                applyCatalogUpdate(normalized);
            } catch (error) {
                console.warn("Unable to refresh services catalog", error);
            }
        };

        bindServiceButtons();
        renderServiceStep();
        updateMiniCart();
        hydrateContactFields();

        // Expose flow entry points so domestic pricing CTA handler can reach them
        window.openPostcodeModal = openPostcodeModal;
        window.openServiceModal = openServiceModal;

        // Allow external code (domestic CTA) to inject a domestic plan into the flow state
        window.setDomesticPlanInFlow = function (planData) {
            flowState.domesticPlan = planData;
            // Pre-fill domestic config with defaults
            var rate = parseFloat(planData.price_per_hour) || 0;
            flowState.domesticConfig = {
                plan_id: planData.plan_id,
                plan_name: planData.plan_name,
                price_per_hour: planData.price_per_hour,
                cleaners: 1,
                hours: 3,
                total: rate * 1 * 3
            };
            pendingDomesticEntry = true;
            persistFlowState();
            updateMiniCart();
        };

        (function applyContractPrefillFromQuery() {
            try {
                var params = new URLSearchParams(window.location.search || "");
                var serviceId = Number(params.get("service_id") || 0);
                var frequency = String(params.get("contract_frequency") || "").trim().toLowerCase();
                var serviceDay = String(params.get("service_day") || "").trim();
                if (!serviceId) return;

                if (["weekly", "fortnightly", "monthly"].indexOf(frequency) !== -1) {
                    flowState.schedule.contract_frequency = frequency;
                }
                if (["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"].indexOf(serviceDay) !== -1) {
                    flowState.contract.service_day = serviceDay;
                }
                persistFlowState();

                // Only open now if the catalog is already loaded.
                // If catalog is empty, applyCatalogUpdate will re-fire this after fetch completes.
                if (!SERVICE_CATALOG.length) return;

                setTimeout(function () {
                    if (askForPostcode && typeof window.openPostcodeModal === "function") {
                        window.openPostcodeModal(serviceId);
                    } else {
                        openServiceModal(serviceId);
                    }
                }, 120);
            } catch (error) {
                console.warn("Unable to apply contract prefill", error);
            }
        })();

        if (!SERVICE_CATALOG.length) {
            fetchCatalogFromApi();
        }

        var closeElements = serviceModal.querySelectorAll("[data-close-modal]");
        closeElements.forEach(function (element) {
            element.addEventListener("click", function () {
                closeServiceModal();
            });
        });
    }

    // Job Application Modal
    var jobModal = document.getElementById("job-modal");
    var jobButtons = document.querySelectorAll("[data-job]");
    var jobTitlePlaceholder = document.getElementById("job-title-placeholder");
    var jobPositionInput = document.getElementById("job-position");
    var jobForm = document.getElementById("job-application-form");
    var jobResumeInput = document.getElementById("applicant-resume");

    if (jobModal && jobButtons.length) {
        var jobCloseElements = jobModal.querySelectorAll("[data-close-modal]");
        var openModal = function (jobTitle) {
            if (jobTitlePlaceholder) jobTitlePlaceholder.textContent = jobTitle;
            if (jobPositionInput) jobPositionInput.value = jobTitle;
            jobModal.classList.add("is-open");
            jobModal.setAttribute("aria-hidden", "false");
            document.body.style.overflow = "hidden"; // Prevent background scrolling
            sendAnalyticsEvent("job_view", { position: jobTitle });
        };

        var closeModal = function () {
            jobModal.classList.remove("is-open");
            jobModal.setAttribute("aria-hidden", "true");
            document.body.style.overflow = "";
            if (jobForm) jobForm.reset();
            if (jobResumeInput) jobResumeInput.value = "";
            var feedback = jobForm.querySelector(".form-feedback");
            if (feedback) {
                feedback.textContent = "";
                feedback.className = "form-feedback";
            }
        };

        // Handle form submission
        if (jobForm) {
            jobForm.addEventListener("submit", async function (e) {
                e.preventDefault();
                var feedback = jobForm.querySelector(".form-feedback");
                if (!feedback) return;

                var btn = jobForm.querySelector("button[type='submit']");
                var originalText = btn.textContent;
                btn.textContent = "Sending...";
                btn.disabled = true;

                try {
                    var payload = new FormData();
                    payload.append("request_type", "job");
                    payload.append("source", "job-modal");
                    payload.append("context_page", window.location.pathname);
                    payload.append("name", jobForm.elements.name.value.trim());
                    payload.append("email", jobForm.elements.email.value.trim());
                    payload.append("phone", jobForm.elements.phone.value.trim());
                    payload.append("position", jobForm.elements.position.value.trim());
                    payload.append("message", jobForm.elements.message.value.trim());

                    if (jobResumeInput && jobResumeInput.files && jobResumeInput.files[0]) {
                        payload.append("resume", jobResumeInput.files[0]);
                    }

                    const response = await fetch(apiBase + "/api/requests", {
                        method: "POST",
                        body: payload
                    });

                    const data = await response.json().catch(function () { return {}; });

                    if (response.ok) {
                        feedback.textContent = data.message || "Application sent successfully! We'll be in touch.";
                        feedback.classList.add("is-success");
                        jobForm.reset();
                        sendAnalyticsEvent("request_submission", { form: "job", request_id: data.request_id });
                        setTimeout(closeModal, 2000);
                    } else {
                        feedback.textContent = data.error || "Submission failed.";
                        feedback.classList.add("is-error");
                    }
                } catch (error) {
                    feedback.textContent = "Network error. Please try again.";
                    feedback.classList.add("is-error");
                    console.error("Job application failed", error);
                } finally {
                    btn.textContent = originalText;
                    btn.disabled = false;
                }
            });
        }

        jobButtons.forEach(function (button) {
            button.addEventListener("click", function (e) {
                e.preventDefault();
                var jobTitle = button.getAttribute("data-job");
                openModal(jobTitle);
            });
        });

        jobCloseElements.forEach(function (el) {
            el.addEventListener("click", closeModal);
        });
    }

    // Domestic Cleaning "Book This Service" — hook into existing booking flow
    var domesticBookButtons = document.querySelectorAll("[data-domestic-book]");
    if (domesticBookButtons.length) {
        domesticBookButtons.forEach(function (btn) {
            btn.addEventListener("click", function (e) {
                e.preventDefault();
                var planId = btn.getAttribute("data-plan-id") || "";
                var planName = btn.getAttribute("data-plan-name") || "";
                var planPrice = btn.getAttribute("data-plan-price") || "";

                var domesticPlanData = {
                    plan_id: planId,
                    plan_name: planName,
                    price_per_hour: planPrice
                };

                // Store in global backup and also inject into flow state
                window.__domesticPlanContext = domesticPlanData;

                if (typeof window.setDomesticPlanInFlow === "function") {
                    window.setDomesticPlanInFlow(domesticPlanData);
                }

                sendAnalyticsEvent("domestic_booking_open", { plan: planName, plan_id: planId });

                // Trigger existing booking flow (postcode gate → service modal)
                var firstServiceId = (Array.isArray(window.SERVICE_CATALOG) && window.SERVICE_CATALOG.length)
                    ? window.SERVICE_CATALOG[0].id
                    : null;

                if (typeof window.openPostcodeModal === "function") {
                    window.openPostcodeModal(firstServiceId);
                } else if (typeof window.openServiceModal === "function") {
                    window.openServiceModal(firstServiceId);
                } else {
                    // Fallback: find and click the first service trigger
                    var trigger = document.querySelector(".service-request-trigger");
                    if (trigger) trigger.click();
                }
            });
        });
    }

    var drawerToggles = document.querySelectorAll("[data-drawer-toggle]");

    if (drawerToggles.length) {
        drawerToggles.forEach(function (button) {
            var targetId = button.getAttribute("data-drawer-toggle");
            if (!targetId) {
                return;
            }

            var target = document.getElementById(targetId);
            if (!target) {
                return;
            }

            var expandLabel = button.getAttribute("data-drawer-expand-label") || "View more";
            var collapseLabel = button.getAttribute("data-drawer-collapse-label") || "Show less";

            var setExpandedState = function (isExpanded) {
                if (isExpanded) {
                    target.classList.add("is-open");
                    target.setAttribute("aria-hidden", "false");
                    button.setAttribute("aria-expanded", "true");
                    button.textContent = collapseLabel;

                    var targetHeight = target.scrollHeight;
                    target.style.maxHeight = targetHeight + "px";

                    window.requestAnimationFrame(function () {
                        // Allow the drawer to expand to accommodate responsive changes
                        target.style.maxHeight = targetHeight + "px";
                    });

                    if (!target.dataset.analyticsLogged) {
                        if (targetId === "services-drawer") {
                            sendAnalyticsEvent("service_view", { context: "drawer" });
                        } else if (targetId === "careers-drawer") {
                            sendAnalyticsEvent("job_view", { context: "drawer" });
                        }
                        target.dataset.analyticsLogged = "true";
                    }
                } else {
                    if (target.style.maxHeight === "none") {
                        target.style.maxHeight = target.scrollHeight + "px";
                        // Force reflow to ensure transition starts from full height
                        void target.offsetHeight;
                    }

                    target.classList.remove("is-open");
                    target.setAttribute("aria-hidden", "true");
                    button.setAttribute("aria-expanded", "false");
                    button.textContent = expandLabel;
                    target.style.maxHeight = "0px";
                }
            };

            setExpandedState(false);

            var toggleDrawer = function () {
                var currentlyExpanded = target.classList.contains("is-open");
                setExpandedState(!currentlyExpanded);
            };

            button.addEventListener("click", function () {
                toggleDrawer();
            });

            window.addEventListener("resize", function () {
                if (target.classList.contains("is-open")) {
                    target.style.maxHeight = target.scrollHeight + "px";
                }
            });

            target.addEventListener("transitionend", function (event) {
                if (event.propertyName === "max-height") {
                    if (target.classList.contains("is-open")) {
                        target.style.maxHeight = "none";
                    }
                }
            });
        });
    }

    var testimonialSlider = document.getElementById("testimonial-slider");

    if (testimonialSlider) {
        var sliderCards = Array.from(testimonialSlider.querySelectorAll(".testimonial-card"));
        var sliderContainer = testimonialSlider.closest(".testimonial-slider-container");
        var prevBtn = sliderContainer ? sliderContainer.querySelector(".slider-nav--prev") : null;
        var nextBtn = sliderContainer ? sliderContainer.querySelector(".slider-nav--next") : null;
        var dotsContainer = sliderContainer ? sliderContainer.querySelector(".slider-dots") : null;

        if (sliderCards.length > 3 && !prefersReducedMotion) {
            var slideDelay = 4000; // Faster: 4 seconds instead of 6.5
            var sliderIntervalId = null;
            var totalSlides = Math.ceil(sliderCards.length / 2);
            var currentSlide = 0;
            var track = document.createElement("div");
            track.className = "testimonial-slider__track";
            var slides = [];

            for (var index = 0; index < totalSlides; index++) {
                var startIndex = index * 2;
                var slideWrapper = document.createElement("div");
                slideWrapper.className = "testimonial-slide";
                var pairedCards = sliderCards.slice(startIndex, startIndex + 2);

                var pairWrapper = document.createElement("div");
                pairWrapper.className = "testimonial-pair";

                pairedCards.forEach(function (card) {
                    pairWrapper.appendChild(card);
                    card.setAttribute("role", "group");
                    card.setAttribute("aria-roledescription", "testimonial");
                    var cite = card.querySelector("cite");
                    var label = cite ? cite.textContent.trim() : "Client testimonial";
                    card.setAttribute("aria-label", label || "Client testimonial");
                });

                if (pairedCards.length === 1) {
                    slideWrapper.classList.add("is-single");
                }

                slideWrapper.appendChild(pairWrapper);

                var firstCardIndex = startIndex + 1;
                var lastCardIndex = startIndex + pairedCards.length;
                slideWrapper.setAttribute("role", "group");
                slideWrapper.setAttribute("aria-roledescription", "slide");
                slideWrapper.setAttribute(
                    "aria-label",
                    pairedCards.length === 2
                        ? "Testimonials " + firstCardIndex + " and " + lastCardIndex + " of " + sliderCards.length
                        : "Testimonial " + firstCardIndex + " of " + sliderCards.length
                );

                track.appendChild(slideWrapper);
                slides.push({ element: slideWrapper, cards: pairedCards });
            }

            while (testimonialSlider.firstChild) {
                testimonialSlider.removeChild(testimonialSlider.firstChild);
            }

            testimonialSlider.classList.add("is-enhanced");
            testimonialSlider.setAttribute("tabindex", "0");
            testimonialSlider.setAttribute("aria-roledescription", "carousel");

            var viewport = document.createElement("div");
            viewport.className = "testimonial-slider__viewport";
            viewport.appendChild(track);
            testimonialSlider.appendChild(viewport);
            track.style.transform = "translateX(0%)";

            var updateSliderHeight = function () {
                var activeSlide = slides[currentSlide];
                if (!activeSlide || !activeSlide.element) {
                    return;
                }
                var computed = window.getComputedStyle(viewport);
                var paddingTop = parseFloat(computed.paddingTop) || 0;
                var paddingBottom = parseFloat(computed.paddingBottom) || 0;
                var targetHeight = activeSlide.element.offsetHeight + paddingTop + paddingBottom;
                viewport.style.height = targetHeight + "px";
                testimonialSlider.style.height = targetHeight + "px";
            };

            var updateAriaStates = function () {
                slides.forEach(function (slide, slideIndex) {
                    var isActive = slideIndex === currentSlide;
                    slide.element.classList.toggle("is-active", isActive);
                    slide.element.setAttribute("aria-hidden", isActive ? "false" : "true");
                    slide.cards.forEach(function (card) {
                        card.setAttribute("aria-hidden", isActive ? "false" : "true");
                    });
                });
            };

            var goToSlide = function (nextIndex) {
                if (nextIndex === currentSlide || nextIndex < 0 || nextIndex >= slides.length) {
                    return;
                }

                currentSlide = nextIndex;
                track.style.transform = "translateX(-" + nextIndex * 100 + "%)";
                updateAriaStates();
                window.requestAnimationFrame(updateSliderHeight);
            };

            var nextSlide = function () {
                var nextIndex = (currentSlide + 1) % slides.length;
                goToSlide(nextIndex);
            };

            var prevSlide = function () {
                var prevIndex = (currentSlide - 1 + slides.length) % slides.length;
                goToSlide(prevIndex);
            };

            var stopAutoPlay = function () {
                if (sliderIntervalId) {
                    window.clearInterval(sliderIntervalId);
                    sliderIntervalId = null;
                }
            };

            var beginAutoPlay = function () {
                if (slides.length <= 1) {
                    return;
                }
                stopAutoPlay();
                sliderIntervalId = window.setInterval(nextSlide, slideDelay);
            };

            // Create navigation dots
            var updateDots = function () {
                if (!dotsContainer) return;
                var dots = dotsContainer.querySelectorAll(".slider-dot");
                dots.forEach(function (dot, index) {
                    dot.classList.toggle("is-active", index === currentSlide);
                    dot.setAttribute("aria-selected", index === currentSlide ? "true" : "false");
                });
            };

            if (dotsContainer && slides.length > 1) {
                slides.forEach(function (slide, index) {
                    var dot = document.createElement("button");
                    dot.className = "slider-dot" + (index === 0 ? " is-active" : "");
                    dot.setAttribute("role", "tab");
                    dot.setAttribute("aria-label", "Go to slide " + (index + 1));
                    dot.setAttribute("aria-selected", index === 0 ? "true" : "false");
                    dot.addEventListener("click", function () {
                        stopAutoPlay();
                        goToSlide(index);
                        updateDots();
                        beginAutoPlay();
                    });
                    dotsContainer.appendChild(dot);
                });
            }

            // Override goToSlide to update dots
            var originalGoToSlide = goToSlide;
            goToSlide = function (nextIndex) {
                originalGoToSlide(nextIndex);
                updateDots();
            };

            // Connect prev/next buttons
            if (prevBtn) {
                prevBtn.addEventListener("click", function () {
                    stopAutoPlay();
                    prevSlide();
                    beginAutoPlay();
                });
            }

            if (nextBtn) {
                nextBtn.addEventListener("click", function () {
                    stopAutoPlay();
                    nextSlide();
                    beginAutoPlay();
                });
            }

            // Swipe/touch support
            var touchStartX = 0;
            var touchEndX = 0;
            var minSwipeDistance = 50;

            testimonialSlider.addEventListener("touchstart", function (e) {
                touchStartX = e.changedTouches[0].screenX;
                // Hide swipe hint after first interaction
                if (sliderContainer) {
                    sliderContainer.classList.add("has-interacted");
                }
            }, { passive: true });

            testimonialSlider.addEventListener("touchend", function (e) {
                touchEndX = e.changedTouches[0].screenX;
                var swipeDistance = touchEndX - touchStartX;
                
                if (Math.abs(swipeDistance) > minSwipeDistance) {
                    stopAutoPlay();
                    if (swipeDistance < 0) {
                        // Swiped left - go next
                        nextSlide();
                    } else {
                        // Swiped right - go prev
                        prevSlide();
                    }
                    beginAutoPlay();
                }
            }, { passive: true });

            updateAriaStates();
            window.requestAnimationFrame(updateSliderHeight);
            beginAutoPlay();

            window.addEventListener("resize", updateSliderHeight);
            window.addEventListener("load", updateSliderHeight, { once: true });

            testimonialSlider.addEventListener("pointerenter", stopAutoPlay);
            testimonialSlider.addEventListener("pointerleave", beginAutoPlay);
            testimonialSlider.addEventListener("focusin", stopAutoPlay);
            testimonialSlider.addEventListener("focusout", function (event) {
                if (!testimonialSlider.contains(event.relatedTarget)) {
                    beginAutoPlay();
                }
            });

            testimonialSlider.addEventListener("keydown", function (event) {
                if (event.key === "ArrowLeft") {
                    event.preventDefault();
                    stopAutoPlay();
                    prevSlide();
                } else if (event.key === "ArrowRight") {
                    event.preventDefault();
                    stopAutoPlay();
                    nextSlide();
                }
            });

            document.addEventListener("visibilitychange", function () {
                if (document.hidden) {
                    stopAutoPlay();
                } else {
                    beginAutoPlay();
                }
            });
            
            // Initialize read more buttons after slider is set up
            setTimeout(initTestimonialReadMore, 100);
        }
    }

    // --- TESTIMONIAL READ MORE MODAL ---
    var testimonialModal = document.getElementById("testimonialModal");
    var testimonialModalRating = document.getElementById("testimonialModalRating");
    var testimonialModalMessage = document.getElementById("testimonialModalMessage");
    var testimonialModalAuthor = document.getElementById("testimonialModalAuthor");

    var openTestimonialModal = function (rating, message, author, isVerified) {
        if (!testimonialModal) return;
        
        // Set content
        if (testimonialModalRating) {
            var stars = "";
            for (var i = 1; i <= 5; i++) {
                stars += i <= rating ? "★" : "☆";
            }
            testimonialModalRating.textContent = stars;
        }
        if (testimonialModalMessage) {
            testimonialModalMessage.textContent = '"' + message + '"';
            // Reset scroll position
            testimonialModalMessage.scrollTop = 0;
        }
        if (testimonialModalAuthor) {
            testimonialModalAuthor.innerHTML = '— ' + author + (isVerified ? ' <span class="verified-badge" title="Verified Customer">✓</span>' : '');
        }
        
        // Open modal
        testimonialModal.classList.add("is-open");
        testimonialModal.setAttribute("aria-hidden", "false");
        document.body.style.overflow = "hidden";
        
        // Auto-scroll long messages after a short delay
        setTimeout(function () {
            if (testimonialModalMessage && testimonialModalMessage.scrollHeight > testimonialModalMessage.clientHeight) {
                var scrollDistance = testimonialModalMessage.scrollHeight - testimonialModalMessage.clientHeight;
                var scrollDuration = Math.max(3000, scrollDistance * 20); // ~20ms per pixel, min 3 seconds
                var startTime = null;
                
                var animateScroll = function (currentTime) {
                    if (!startTime) startTime = currentTime;
                    var elapsed = currentTime - startTime;
                    var progress = Math.min(elapsed / scrollDuration, 1);
                    
                    // Ease-in-out for smoother scrolling
                    var easeProgress = progress < 0.5
                        ? 2 * progress * progress
                        : 1 - Math.pow(-2 * progress + 2, 2) / 2;
                    
                    testimonialModalMessage.scrollTop = easeProgress * scrollDistance;
                    
                    if (progress < 1 && testimonialModal.classList.contains("is-open")) {
                        requestAnimationFrame(animateScroll);
                    }
                };
                
                requestAnimationFrame(animateScroll);
            }
        }, 800);
    };

    var closeTestimonialModal = function () {
        if (!testimonialModal) return;
        testimonialModal.classList.remove("is-open");
        testimonialModal.setAttribute("aria-hidden", "true");
        document.body.style.overflow = "";
    };

    // Close modal handlers
    if (testimonialModal) {
        testimonialModal.querySelectorAll("[data-close-modal]").forEach(function (el) {
            el.addEventListener("click", closeTestimonialModal);
        });
        testimonialModal.addEventListener("keydown", function (e) {
            if (e.key === "Escape") closeTestimonialModal();
        });
    }

    var initTestimonialReadMore = function () {
        var testimonialCards = document.querySelectorAll(".testimonial-card");
        
        testimonialCards.forEach(function (card) {
            var message = card.querySelector(".testimonial-message");
            var readMoreBtn = card.querySelector(".testimonial-read-more");
            
            if (!message || !readMoreBtn) return;
            
            // Skip if already initialized
            if (readMoreBtn.dataset.initialized) return;
            readMoreBtn.dataset.initialized = "true";
            
            // Check if text is truncated using multiple methods
            var checkTruncation = function () {
                // Reset state first
                message.classList.remove("is-expanded");
                readMoreBtn.classList.remove("is-expanded");
                
                // Method 1: Check text length (show if more than ~150 chars)
                var textLength = message.textContent.trim().length;
                var shouldShowByLength = textLength > 150;
                
                // Method 2: Check scroll vs client height
                var scrollHeight = message.scrollHeight;
                var clientHeight = message.clientHeight;
                var shouldShowByHeight = scrollHeight > clientHeight + 2;
                
                // Show button if either condition is met
                if (shouldShowByLength || shouldShowByHeight) {
                    readMoreBtn.style.display = "inline-flex";
                } else {
                    readMoreBtn.style.display = "none";
                }
            };
            
            // Check after delays to ensure CSS is applied
            setTimeout(checkTruncation, 50);
            setTimeout(checkTruncation, 300);
            window.addEventListener("resize", checkTruncation);
            
            // Open modal with full testimonial
            readMoreBtn.addEventListener("click", function (e) {
                e.stopPropagation();
                
                // Get testimonial data from card
                var ratingEl = card.querySelector(".testimonial-rating");
                var authorEl = card.querySelector("cite");
                var verifiedBadge = card.querySelector(".verified-badge");
                
                // Count filled stars to get numeric rating
                var ratingText = ratingEl ? ratingEl.textContent.trim() : "";
                var numericRating = (ratingText.match(/★/g) || []).length;
                
                var fullMessage = message.getAttribute("data-full-text") || message.textContent.trim();
                // Remove surrounding quotes if present
                fullMessage = fullMessage.replace(/^[""]|[""]$/g, "").trim();
                
                var author = authorEl ? authorEl.textContent.trim() : "";
                // Remove verified badge symbol from author name if present
                author = author.replace(/\s*✓\s*$/, "").trim();
                
                var isVerified = !!verifiedBadge;
                
                openTestimonialModal(numericRating, fullMessage, author, isVerified);
            });
        });
    };
    
    // Run on DOMContentLoaded and also after load
    initTestimonialReadMore();
    window.addEventListener("load", function () {
        setTimeout(initTestimonialReadMore, 300);
    });

    // ─────────────────────────────────────────────────────────────────────────────
    // UNIFIED FLOATING ACTION BUTTON (FAB)
    // ─────────────────────────────────────────────────────────────────────────────
    var fabContainer = document.getElementById('fabContainer');
    var fabToggle = document.getElementById('fabToggle');
    var fabActions = document.getElementById('fabActions');
    var fabBackToTop = document.getElementById('fabBackToTop');
    var fabWhatsApp = document.getElementById('fabWhatsApp');
    var fabChat = document.getElementById('fabChat');

    // Toggle FAB menu
    function toggleFab() {
        if (!fabContainer) return;
        var isOpen = fabContainer.classList.toggle('is-open');
        if (fabToggle) {
            fabToggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        }
    }

    function closeFab() {
        if (!fabContainer) return;
        fabContainer.classList.remove('is-open');
        if (fabToggle) {
            fabToggle.setAttribute('aria-expanded', 'false');
        }
    }

    // FAB Toggle click
    if (fabToggle) {
        fabToggle.addEventListener('click', toggleFab);
    }

    // Back to Top action
    if (fabBackToTop) {
        fabBackToTop.addEventListener('click', function (e) {
            e.preventDefault();
            closeFab();
            window.scrollTo({
                top: 0,
                behavior: 'smooth'
            });
        });
    }

    // Chat action - opens the chat widget
    if (fabChat) {
        fabChat.addEventListener('click', function (e) {
            e.preventDefault();
            closeFab();
            // Hide FAB when chat opens
            if (fabContainer) {
                fabContainer.classList.add('chat-active');
            }
            openChat();
        });
    }

    // Close FAB when clicking outside
    document.addEventListener('click', function (e) {
        if (fabContainer && fabContainer.classList.contains('is-open')) {
            if (!fabContainer.contains(e.target)) {
                closeFab();
            }
        }
    });

    // Close FAB on scroll (optional, for cleaner UX)
    var fabScrollTimeout;
    window.addEventListener('scroll', function () {
        if (fabContainer && fabContainer.classList.contains('is-open')) {
            clearTimeout(fabScrollTimeout);
            fabScrollTimeout = setTimeout(closeFab, 150);
        }
    }, { passive: true });

    // --- COMPANY PROFILE MODAL ---
    var companyProfileModal = document.getElementById("companyProfileModal");
    var companyProfileOpenBtns = document.querySelectorAll("[data-open-company-profile]");
    var companyProfileCloseBtns = companyProfileModal ? companyProfileModal.querySelectorAll("[data-close-modal]") : [];
    var profileTabs = companyProfileModal ? companyProfileModal.querySelectorAll(".profile-tab") : [];
    var profilePanels = companyProfileModal ? companyProfileModal.querySelectorAll(".profile-panel") : [];

    function openCompanyProfileModal(targetTab) {
        if (!companyProfileModal) return;
        companyProfileModal.classList.add("is-open");
        body.style.overflow = "hidden";
        if (targetTab) {
            switchProfileTab(targetTab);
        }
        var firstCloseBtn = companyProfileModal.querySelector("[data-close-modal]");
        if (firstCloseBtn) {
            firstCloseBtn.focus();
        }
    }

    function closeCompanyProfileModal() {
        if (!companyProfileModal) return;
        companyProfileModal.classList.remove("is-open");
        body.style.overflow = "";
    }

    function switchProfileTab(tabId) {
        profileTabs.forEach(function (tab) {
            if (tab.getAttribute("data-tab") === tabId) {
                tab.classList.add("active");
                tab.setAttribute("aria-selected", "true");
            } else {
                tab.classList.remove("active");
                tab.setAttribute("aria-selected", "false");
            }
        });
        profilePanels.forEach(function (panel) {
            if (panel.id === tabId) {
                panel.classList.add("active");
                panel.removeAttribute("hidden");
            } else {
                panel.classList.remove("active");
                panel.setAttribute("hidden", "");
            }
        });
    }

    companyProfileOpenBtns.forEach(function (btn) {
        btn.addEventListener("click", function (e) {
            e.preventDefault();
            var targetTab = btn.getAttribute("data-open-company-profile") || "profile-story";
            openCompanyProfileModal(targetTab);
        });
    });

    companyProfileCloseBtns.forEach(function (btn) {
        btn.addEventListener("click", function (e) {
            e.preventDefault();
            closeCompanyProfileModal();
        });
    });

    if (companyProfileModal) {
        companyProfileModal.addEventListener("click", function (e) {
            if (e.target === companyProfileModal) {
                closeCompanyProfileModal();
            }
        });

        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape" && companyProfileModal.classList.contains("is-open")) {
                closeCompanyProfileModal();
            }
        });
    }

    profileTabs.forEach(function (tab) {
        tab.addEventListener("click", function (e) {
            e.preventDefault();
            var tabId = tab.getAttribute("data-tab");
            switchProfileTab(tabId);
        });

        tab.addEventListener("keydown", function (e) {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                var tabId = tab.getAttribute("data-tab");
                switchProfileTab(tabId);
            }
        });
    });

    // ─────────────────────────────────────────────────────────────────────────────
    // FAQ Accordion
    // ─────────────────────────────────────────────────────────────────────────────
    var faqItems = Array.from(document.querySelectorAll('[data-faq]'));
    var faqShowMoreButton = document.getElementById('faq-show-more');
    var faqAccordion = document.querySelector('.faq-accordion');
    var faqPreviewLimit = 6;

    var closeFaqItem = function (faqItem) {
        var button = faqItem.querySelector('.faq-item__question');
        var answer = faqItem.querySelector('.faq-item__answer');
        if (button && answer) {
            button.setAttribute('aria-expanded', 'false');
            answer.hidden = true;
        }
    };

    if (!faqShowMoreButton && faqAccordion && faqItems.length > faqPreviewLimit) {
        var extraFaqCount = faqItems.length - faqPreviewLimit;
        var footer = document.createElement('div');
        footer.className = 'faq-accordion__footer';

        faqShowMoreButton = document.createElement('button');
        faqShowMoreButton.type = 'button';
        faqShowMoreButton.className = 'button button--ghost faq-accordion__toggle';
        faqShowMoreButton.id = 'faq-show-more';
        faqShowMoreButton.setAttribute('aria-expanded', 'false');
        faqShowMoreButton.setAttribute('data-expanded-label', 'Show fewer FAQs');
        faqShowMoreButton.setAttribute('data-collapsed-label', 'View ' + extraFaqCount + ' more FAQ' + (extraFaqCount === 1 ? '' : 's'));
        faqShowMoreButton.textContent = faqShowMoreButton.getAttribute('data-collapsed-label');

        footer.appendChild(faqShowMoreButton);
        faqAccordion.appendChild(footer);
    }

    if (faqItems.length > faqPreviewLimit) {
        faqItems.forEach(function (item, index) {
            var isExtra = index >= faqPreviewLimit;
            item.classList.toggle('faq-item--extra', isExtra);
            if (isExtra) {
                item.hidden = true;
                closeFaqItem(item);
            }
        });
    }

    if (faqShowMoreButton) {
        if (!faqShowMoreButton.hasAttribute('data-expanded-label')) {
            faqShowMoreButton.setAttribute('data-expanded-label', 'Show fewer FAQs');
        }
        if (!faqShowMoreButton.hasAttribute('data-collapsed-label')) {
            var collapsedCount = Math.max(0, faqItems.length - faqPreviewLimit);
            faqShowMoreButton.setAttribute('data-collapsed-label', 'View ' + collapsedCount + ' more FAQ' + (collapsedCount === 1 ? '' : 's'));
        }
        faqShowMoreButton.setAttribute('aria-expanded', 'false');
        faqShowMoreButton.textContent = faqShowMoreButton.getAttribute('data-collapsed-label');

        faqShowMoreButton.addEventListener('click', function () {
            var isExpanded = faqShowMoreButton.getAttribute('aria-expanded') === 'true';
            var extraItems = faqItems.filter(function (item) { return item.classList.contains('faq-item--extra'); });
            var expandedLabel = faqShowMoreButton.getAttribute('data-expanded-label') || 'Show fewer FAQs';
            var collapsedLabel = faqShowMoreButton.getAttribute('data-collapsed-label') || 'View more FAQs';

            extraItems.forEach(function (item) {
                if (isExpanded) {
                    item.hidden = true;
                    closeFaqItem(item);
                } else {
                    item.hidden = false;
                }
            });

            faqShowMoreButton.setAttribute('aria-expanded', isExpanded ? 'false' : 'true');
            faqShowMoreButton.textContent = isExpanded ? collapsedLabel : expandedLabel;
        });
    }

    faqItems.forEach(function (item) {
        var questionBtn = item.querySelector('.faq-item__question');
        var answerDiv = item.querySelector('.faq-item__answer');

        if (questionBtn && answerDiv) {
            questionBtn.addEventListener('click', function () {
                var isExpanded = questionBtn.getAttribute('aria-expanded') === 'true';

                // Close all other FAQ items
                faqItems.forEach(function (otherItem) {
                    if (otherItem !== item) {
                        closeFaqItem(otherItem);
                    }
                });

                // Toggle current item
                questionBtn.setAttribute('aria-expanded', !isExpanded);
                answerDiv.hidden = isExpanded;
            });
        }
    });

    // ─────────────────────────────────────────────────────────────────────────────
    // AI CHAT WIDGET
    // ─────────────────────────────────────────────────────────────────────────────
    var chatWidget = document.getElementById('chatWidget');
    var chatToggle = document.getElementById('chatToggle');
    var chatWindow = document.getElementById('chatWindow');
    var chatMinimize = document.getElementById('chatMinimize');
    var chatForm = document.getElementById('chatForm');
    var chatInput = document.getElementById('chatInput');
    var chatMessages = document.getElementById('chatMessages');
    var chatSuggestions = document.getElementById('chatSuggestions');
    var chatPersonaName = document.getElementById('chatPersonaName');
    var chatGreeting = document.getElementById('chatGreeting');
    var chatWelcome = document.getElementById('chatWelcome');
    var openChatFromFaq = document.getElementById('openChatFromFaq');

    var chatSessionId = null;
    var chatPersona = null;
    var chatInitialized = false;
    var isWaitingForResponse = false;

    // Initialize chat when opened
    function initializeChat() {
        if (chatInitialized) return Promise.resolve();

        return fetch('/api/chat/init', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        })
        .then(function(response) {
            if (!response.ok) throw new Error('Chat unavailable');
            return response.json();
        })
        .then(function(data) {
            chatSessionId = data.session_id;
            chatPersona = data.persona;
            chatInitialized = true;

            // Update UI with persona info
            if (chatPersonaName && chatPersona.name) {
                chatPersonaName.textContent = chatPersona.name;
            }
            if (chatGreeting && chatPersona.greeting) {
                chatGreeting.textContent = chatPersona.greeting;
            }
            if (chatPersona.avatar && document.getElementById('chatAvatar')) {
                document.getElementById('chatAvatar').innerHTML = '<img src="' + chatPersona.avatar + '" alt="' + chatPersona.name + '">';
            }
        })
        .catch(function(error) {
            console.error('Chat init error:', error);
            showChatError('Chat is currently unavailable. Please try again later.');
            return Promise.reject(error);
        });
    }

    // Helpers to manage the widget state
    function openChat() {
        if (!chatWidget || chatWidget.classList.contains('is-open')) {
            return;
        }
        chatWidget.classList.add('is-open');
        chatWidget.setAttribute('aria-hidden', 'false');
        if (body) {
            body.classList.add('chat-widget-open');
        }
        initializeChat().then(function() {
            if (chatInput) chatInput.focus();
        }).catch(function() {
            // Error already handled during initialization
        });
    }

    function closeChat() {
        if (!chatWidget) {
            return;
        }
        chatWidget.classList.remove('is-open');
        chatWidget.setAttribute('aria-hidden', 'true');
        if (body) {
            body.classList.remove('chat-widget-open');
        }
        // Show FAB again when chat closes
        var fabContainer = document.getElementById('fabContainer');
        if (fabContainer) {
            fabContainer.classList.remove('chat-active');
        }
    }

    function toggleChat() {
        if (!chatWidget) {
            return;
        }
        if (chatWidget.classList.contains('is-open')) {
            closeChat();
        } else {
            openChat();
        }
    }

    // Add message to chat
    function addChatMessage(content, isUser) {
        // Hide welcome message on first message
        if (chatWelcome && !chatWelcome.hidden) {
            chatWelcome.hidden = true;
        }

        var messageDiv = document.createElement('div');
        messageDiv.className = 'chat-message ' + (isUser ? 'chat-message--user' : 'chat-message--assistant');

        var avatarDiv = document.createElement('div');
        avatarDiv.className = 'chat-message__avatar';
        if (isUser) {
            avatarDiv.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';
        } else {
            avatarDiv.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/></svg>';
        }

        var contentDiv = document.createElement('div');
        contentDiv.className = 'chat-message__content';
        // Convert newlines to <br> for proper formatting, but escape HTML first
        var escaped = content.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        contentDiv.innerHTML = escaped.replace(/\n/g, '<br>');

        messageDiv.appendChild(avatarDiv);
        messageDiv.appendChild(contentDiv);
        chatMessages.appendChild(messageDiv);

        // Scroll to bottom
        chatMessages.scrollTop = chatMessages.scrollHeight;

        return messageDiv;
    }

    // Show typing indicator
    function showTypingIndicator() {
        var typingDiv = document.createElement('div');
        typingDiv.className = 'chat-message chat-message--assistant';
        typingDiv.id = 'chatTyping';

        typingDiv.innerHTML = '<div class="chat-message__avatar"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/></svg></div><div class="chat-message__content"><div class="chat-message__typing"><span></span><span></span><span></span></div></div>';

        chatMessages.appendChild(typingDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    // Hide typing indicator
    function hideTypingIndicator() {
        var typing = document.getElementById('chatTyping');
        if (typing) typing.remove();
    }

    // Show error in chat
    function showChatError(message) {
        var errorDiv = document.createElement('div');
        errorDiv.className = 'chat-message chat-message--assistant';
        errorDiv.innerHTML = '<div class="chat-message__avatar" style="background:#ef4444"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg></div><div class="chat-message__content" style="background:#fef2f2;color:#991b1b">' + escapeHtml(message) + '</div>';
        chatMessages.appendChild(errorDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    // Send message
    function sendMessage(message) {
        if (!message.trim() || isWaitingForResponse || !chatSessionId) return;

        // Hide suggestions after first message
        if (chatSuggestions) {
            chatSuggestions.style.display = 'none';
        }

        // Add user message
        addChatMessage(message, true);
        
        // Clear input
        if (chatInput) chatInput.value = '';

        // Show typing indicator
        isWaitingForResponse = true;
        showTypingIndicator();

        // Send to API
        fetch('/api/chat/message', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: chatSessionId,
                message: message
            })
        })
        .then(function(response) {
            if (!response.ok) throw new Error('Failed to send message');
            return response.json();
        })
        .then(function(data) {
            hideTypingIndicator();
            isWaitingForResponse = false;
            
            if (data.response) {
                addChatMessage(data.response, false);
            } else {
                showChatError('Sorry, I couldn\'t process that. Please try again.');
            }
        })
        .catch(function(error) {
            hideTypingIndicator();
            isWaitingForResponse = false;
            console.error('Chat error:', error);
            showChatError('Something went wrong. Please try again.');
        });
    }

    // Event listeners
    if (chatToggle) {
        chatToggle.addEventListener('click', toggleChat);
    }

    if (chatMinimize) {
        chatMinimize.addEventListener('click', closeChat);
    }

    if (chatForm) {
        chatForm.addEventListener('submit', function(e) {
            e.preventDefault();
            var message = chatInput ? chatInput.value : '';
            sendMessage(message);
        });
    }

    // Quick suggestion buttons
    if (chatSuggestions) {
        chatSuggestions.addEventListener('click', function(e) {
            var btn = e.target.closest('.chat-suggestion');
            if (btn) {
                var message = btn.getAttribute('data-message');
                if (message) sendMessage(message);
            }
        });
    }

    // Open chat from FAQ section
    if (openChatFromFaq) {
        openChatFromFaq.addEventListener('click', openChat);
    }

    // Close chat on escape key
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && chatWidget && chatWidget.classList.contains('is-open')) {
            closeChat();
        }
    });
});


/* ─── HERO SECTION (SLIDIN GH) ─── */
document.addEventListener('DOMContentLoaded', function () {
    var heroEl = document.querySelector('.hero');
    if (!heroEl) return;

    // Set layout mode attribute for CSS
    var layoutMode = heroEl.getAttribute('data-layout') || 'balenciaga';
    heroEl.setAttribute('data-layout', layoutMode);

    // Video playback optimization
    var video = heroEl.querySelector('.hero__video');
    if (video) {
        // On mobile, defer video load so it doesn't block page render
        if (window.innerWidth <= 768) {
            video.removeAttribute('autoplay');
            video.setAttribute('preload', 'none');
            // Load and play after page is fully loaded
            window.addEventListener('load', function () {
                video.setAttribute('preload', 'metadata');
                video.load();
                video.play().catch(function () {});
            });
        }
        video.addEventListener('play', function () {
            video.style.willChange = 'auto';
        });
    }
});

