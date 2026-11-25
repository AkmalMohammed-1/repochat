// Browser-side shopping cart.

export interface CartItem {
  sku: string;
  priceCents: number;
  quantity: number;
}

/** Format integer cents as a dollar string, e.g. 1999 -> "$19.99". */
export function formatPrice(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

export class Cart {
  private items: CartItem[] = [];

  addItem(item: CartItem): void {
    const existing = this.items.find((i) => i.sku === item.sku);
    if (existing) {
      existing.quantity += item.quantity;
    } else {
      this.items.push({ ...item });
    }
  }

  total(): number {
    return this.items.reduce((sum, i) => sum + i.priceCents * i.quantity, 0);
  }
}

export const FREE_SHIPPING_THRESHOLD_CENTS = 5000;
