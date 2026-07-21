'use client';

/** Sheet / Drawer - mobile-first side/bottom panels (Atelier). Built on Radix Dialog. */
import * as React from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import X from 'lucide-react/dist/esm/icons/x';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

export const Sheet = DialogPrimitive.Root;
export const SheetTrigger = DialogPrimitive.Trigger;
export const SheetClose = DialogPrimitive.Close;

const sheetVariants = cva(
  cn(
    'fixed z-50 bg-[var(--card)] text-[var(--card-foreground)] shadow-[var(--shadow-at-e3)]',
    'data-[state=open]:animate-in data-[state=closed]:animate-out'
  ),
  {
    variants: {
      side: {
        right:
          'inset-y-0 right-0 h-full w-80 max-w-[85vw] border-l border-[var(--border)] data-[state=open]:slide-in-from-right data-[state=closed]:slide-out-to-right',
        left: 'inset-y-0 left-0 h-full w-80 max-w-[85vw] border-r border-[var(--border)] data-[state=open]:slide-in-from-left data-[state=closed]:slide-out-to-left',
        bottom:
          'inset-x-0 bottom-0 rounded-t-[var(--radius-at-xl)] border-t border-[var(--border)] max-h-[85vh] data-[state=open]:slide-in-from-bottom data-[state=closed]:slide-out-to-bottom',
      },
    },
    defaultVariants: { side: 'right' },
  }
);

export const SheetContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> &
    VariantProps<typeof sheetVariants>
>(({ className, side, children, ...props }, ref) => (
  <DialogPrimitive.Portal>
    <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0" />
    <DialogPrimitive.Content
      ref={ref}
      // Portaled to document.body - `atelier` makes the sheet resolve Atelier
      // tokens instead of the legacy Swiss :root fallbacks.
      className={cn('atelier', sheetVariants({ side }), className)}
      {...props}
    >
      {children}
      <DialogPrimitive.Close
        className="absolute right-3 top-3 rounded-[var(--radius-at-sm)] p-2 text-[var(--muted-foreground)] hover:bg-[var(--accent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
        aria-label="Close"
      >
        <X className="size-4" />
      </DialogPrimitive.Close>
    </DialogPrimitive.Content>
  </DialogPrimitive.Portal>
));
SheetContent.displayName = 'AtelierSheetContent';

export const SheetTitle = DialogPrimitive.Title;
export const SheetDescription = DialogPrimitive.Description;
