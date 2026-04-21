"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  BarChart3,
  ClipboardCheck,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Menu,
  Shield,
  ShieldBan,
  UploadCloud,
  User,
} from "lucide-react";
import { BillingBadge } from "@/components/BillingBadge";
import { ComplianceBadge } from "@/components/ComplianceBadge";
import { SystemStatus } from "@/components/SystemStatus";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet";
import { ApiError, api } from "@/lib/api";
import {
  type AuthUser,
  clearToken,
  getStoredUser,
  getToken,
  setStoredUser,
} from "@/lib/auth";
import { cn } from "@/lib/utils";

const PUBLIC_PATHS = new Set<string>(["/login", "/pricing"]);

type NavItem = {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
};

const PRIMARY_NAV: NavItem[] = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/bulk", label: "Bulk upload", icon: UploadCloud },
  { href: "/review", label: "Review queue", icon: ClipboardCheck },
  { href: "/blacklist", label: "Blacklist", icon: ShieldBan },
  { href: "/api-keys", label: "API keys", icon: KeyRound },
  { href: "/pricing", label: "Pricing", icon: BarChart3 },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/";
  const isPublic = PUBLIC_PATHS.has(pathname);

  if (isPublic) {
    // Public pages render without the app chrome. Layout gives them a clean
    // full-width canvas so login / pricing can present themselves.
    return <div className="min-h-screen bg-background">{children}</div>;
  }

  return <AuthedShell pathname={pathname}>{children}</AuthedShell>;
}

function AuthedShell({
  pathname,
  children,
}: {
  pathname: string;
  children: React.ReactNode;
}) {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [checked, setChecked] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    // Hydrate the stored user after mount so SSR and first client render match.
    setUser(getStoredUser());
  }, []);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
      return;
    }
    // Verify with backend — catches expired/revoked sessions.
    api
      .me()
      .then((u) => {
        setUser(u);
        setStoredUser(u);
        setChecked(true);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearToken();
          router.replace(`/login?next=${encodeURIComponent(pathname)}`);
        } else {
          setChecked(true);
        }
      });
  }, [pathname, router]);

  async function handleLogout() {
    try {
      await api.logout();
    } catch {
      // Best effort — drop local state regardless.
    }
    clearToken();
    setUser(null);
    router.replace("/login");
  }

  if (!checked) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div className="text-sm text-muted-foreground">Checking session…</div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen bg-background">
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 border-r bg-sidebar text-sidebar-foreground lg:block">
        <SidebarContent pathname={pathname} />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Top bar */}
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b bg-background/80 px-4 backdrop-blur-sm md:px-6">
          <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
            <SheetTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="lg:hidden"
                aria-label="Open navigation"
              >
                <Menu className="h-5 w-5" />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-72 p-0">
              <SidebarContent
                pathname={pathname}
                onNavigate={() => setMobileOpen(false)}
              />
            </SheetContent>
          </Sheet>

          <div className="flex-1" />

          <div className="hidden items-center gap-4 sm:flex">
            <BillingBadge />
            <SystemStatus />
            <ComplianceBadge />
          </div>

          <Separator orientation="vertical" className="hidden h-6 sm:block" />

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className="gap-2 rounded-full pl-1 pr-3"
              >
                <span className="flex h-7 w-7 items-center justify-center rounded-full bg-primary/10 text-primary">
                  <User className="h-4 w-4" />
                </span>
                <span className="hidden max-w-[140px] truncate text-sm md:inline">
                  {user?.email ?? "Account"}
                </span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuLabel className="flex flex-col">
                <span className="text-xs text-muted-foreground">
                  Signed in as
                </span>
                <span className="truncate">{user?.email ?? "—"}</span>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onSelect={() => router.push("/api-keys")}
                className="cursor-pointer"
              >
                <KeyRound className="h-4 w-4" />
                API keys
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => router.push("/pricing")}
                className="cursor-pointer"
              >
                <BarChart3 className="h-4 w-4" />
                Pricing
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onSelect={handleLogout}
                className="cursor-pointer text-destructive focus:text-destructive"
              >
                <LogOut className="h-4 w-4" />
                Sign out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </header>

        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 md:px-8 md:py-8">
          {children}
        </main>
      </div>
    </div>
  );
}

function SidebarContent({
  pathname,
  onNavigate,
}: {
  pathname: string;
  onNavigate?: () => void;
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-14 items-center gap-2 border-b border-sidebar-border px-5">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <Shield className="h-4 w-4" />
        </div>
        <Link
          href="/"
          onClick={onNavigate}
          className="text-sm font-semibold tracking-tight"
        >
          AI LeadGen OS
        </Link>
      </div>

      <nav className="flex-1 space-y-1 p-3">
        {PRIMARY_NAV.map((item) => {
          const isActive =
            item.href === "/"
              ? pathname === "/"
              : pathname === item.href || pathname.startsWith(`${item.href}/`);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={onNavigate}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
              )}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-sidebar-border p-4 text-xs text-muted-foreground">
        <div className="font-medium text-foreground">Compliance first</div>
        <p className="mt-1 leading-relaxed">
          Tier-1 official APIs only. EU/UK GDPR, legitimate interest.
        </p>
      </div>
    </div>
  );
}
