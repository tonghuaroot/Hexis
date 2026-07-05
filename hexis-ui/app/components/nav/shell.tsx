"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Sidebar } from "./sidebar";

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const hideNav = pathname.startsWith("/init");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  // Close the mobile drawer whenever the route changes.
  useEffect(() => {
    setMobileNavOpen(false);
  }, [pathname]);

  // Esc dismisses the mobile drawer.
  useEffect(() => {
    if (!mobileNavOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileNavOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileNavOpen]);

  if (hideNav) {
    return <>{children}</>;
  }

  return (
    <div className="flex min-h-screen">
      {/* Hamburger — only below lg, where the sidebar is a dismissible drawer. */}
      <button
        type="button"
        aria-label="Open navigation menu"
        aria-expanded={mobileNavOpen}
        onClick={() => setMobileNavOpen((open) => !open)}
        className="fixed left-4 top-4 z-30 flex h-10 w-10 items-center justify-center rounded-xl border border-[var(--outline)] bg-[var(--surface)] text-lg text-[var(--foreground)] shadow-sm lg:hidden"
      >
        <span aria-hidden="true">&#9776;</span>
      </button>

      {/* Backdrop — closes the drawer on tap; mobile only. */}
      {mobileNavOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/40 lg:hidden"
          aria-hidden="true"
          onClick={() => setMobileNavOpen(false)}
        />
      )}

      <Sidebar open={mobileNavOpen} onClose={() => setMobileNavOpen(false)} />
      <main className="flex-1 lg:ml-56">{children}</main>
    </div>
  );
}
