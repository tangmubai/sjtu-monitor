import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const variants = cva(
  "appBadge inline-flex h-6 shrink-0 items-center rounded-full border px-2 text-xs font-medium",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        outline: "border-border text-foreground",
        success: "border-emerald-200 bg-emerald-50 text-emerald-700",
        destructive: "border-red-200 bg-red-50 text-red-700",
      },
    },
    defaultVariants: { variant: "secondary" },
  },
);

export function Badge({
  className,
  variant,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof variants>) {
  return <span data-variant={variant || "secondary"} className={cn(variants({ variant }), className)} {...props} />;
}
