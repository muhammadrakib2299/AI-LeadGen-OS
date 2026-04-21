"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApiError, api } from "@/lib/api";
import { getToken } from "@/lib/auth";

interface Plan {
  name: string;
  price: string;
  cadence: string;
  highlight?: boolean;
  cta: "register" | "checkout" | "contact";
  features: string[];
}

const PLANS: Plan[] = [
  {
    name: "Free",
    price: "$0",
    cadence: "forever",
    cta: "register",
    features: [
      "10 jobs per month",
      "100 leads per export",
      "EU/UK Tier-1 sources",
      "CSV export",
      "Compliant Mode + GDPR opt-out",
    ],
  },
  {
    name: "Standard",
    price: "$49",
    cadence: "per month",
    highlight: true,
    cta: "checkout",
    features: [
      "Unlimited jobs",
      "Unlimited leads",
      "Yelp + Foursquare fallbacks",
      "REST API + webhook delivery",
      "HubSpot CRM export",
      "Re-verification scheduler",
    ],
  },
  {
    name: "Custom",
    price: "Talk to us",
    cadence: "",
    cta: "contact",
    features: [
      "Everything in Standard",
      "Volume discounts",
      "Dedicated EU region",
      "Priority support",
      "On-prem deployment option",
    ],
  },
];

export default function PricingPage() {
  const router = useRouter();

  async function handleClick(plan: Plan) {
    if (plan.cta === "register") {
      router.push("/login?mode=register");
      return;
    }
    if (plan.cta === "contact") {
      window.location.href = "mailto:hello@combosoft.co.uk?subject=AI LeadGen OS — Custom plan";
      return;
    }
    // Checkout: must be signed in. Otherwise route to register first.
    if (!getToken()) {
      router.push("/login?mode=register&next=/pricing");
      return;
    }
    try {
      const res = await api.createBillingCheckout();
      window.location.href = res.checkout_url;
    } catch (err) {
      if (err instanceof ApiError) alert(err.detail);
      else alert(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="space-y-10">
      <header className="text-center">
        <h1 className="text-3xl font-semibold tracking-tight">Pricing</h1>
        <p className="mt-2 text-sm text-neutral-600 dark:text-neutral-400">
          Compliant lead generation for EU/UK B2B teams. Cancel anytime.
        </p>
      </header>

      <section className="grid grid-cols-1 gap-6 md:grid-cols-3">
        {PLANS.map((plan) => (
          <div
            key={plan.name}
            className={
              "flex flex-col rounded-lg border p-6 " +
              (plan.highlight
                ? "border-blue-600 bg-blue-50 dark:border-blue-500 dark:bg-blue-950"
                : "border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900")
            }
          >
            <h2 className="text-xl font-semibold">{plan.name}</h2>
            <div className="mt-3 flex items-baseline gap-1">
              <span className="text-3xl font-semibold">{plan.price}</span>
              {plan.cadence && (
                <span className="text-sm text-neutral-500">/ {plan.cadence}</span>
              )}
            </div>
            <ul className="mt-6 flex-1 space-y-2 text-sm">
              {plan.features.map((f) => (
                <li key={f} className="flex items-start gap-2">
                  <span className="mt-0.5 text-emerald-600 dark:text-emerald-400">✓</span>
                  <span>{f}</span>
                </li>
              ))}
            </ul>
            <button
              type="button"
              onClick={() => handleClick(plan)}
              className={
                "mt-6 rounded px-4 py-2 text-sm font-medium " +
                (plan.highlight
                  ? "bg-blue-600 text-white hover:bg-blue-700"
                  : "border border-neutral-300 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800")
              }
            >
              {plan.cta === "checkout"
                ? "Upgrade"
                : plan.cta === "contact"
                  ? "Contact us"
                  : "Get started"}
            </button>
          </div>
        ))}
      </section>

      <footer className="text-center text-xs text-neutral-500">
        <p>
          All plans run on EU infrastructure. Billing in USD via Stripe.{" "}
          <Link href="/" className="underline">
            Back to dashboard
          </Link>
        </p>
      </footer>
    </div>
  );
}
