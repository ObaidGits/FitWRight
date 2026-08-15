import { describe, expect, it } from 'vitest';

/**
 * The empty-vs-zero rule for per-user limit overrides.
 *
 * EMPTY means "inherit the global default". ZERO means "this user gets nothing".
 * Collapsing them would make it impossible to un-restrict someone: clearing the
 * field would silently be read as "give them zero" and they would stay locked out
 * forever, with the UI showing an empty box that looks like no restriction at all.
 *
 * Mirrors the patch-building logic in components/admin/user-credits-panel.tsx.
 */
function buildLimitPatch(allowance: string, velocity: string) {
  return {
    ...(allowance.trim() === ''
      ? { clear_allowance_override: true }
      : { monthly_allowance_override: Number(allowance) }),
    ...(velocity.trim() === ''
      ? { clear_velocity_override: true }
      : { velocity_cap_override: Number(velocity) }),
  };
}

describe('per-user limit overrides', () => {
  it('an empty field CLEARS the override so the global default applies', () => {
    const patch = buildLimitPatch('', '');
    expect(patch).toEqual({
      clear_allowance_override: true,
      clear_velocity_override: true,
    });
    expect('monthly_allowance_override' in patch).toBe(false);
  });

  it('zero SETS the override, restricting the user to nothing', () => {
    const patch = buildLimitPatch('0', '0');
    expect(patch).toEqual({
      monthly_allowance_override: 0,
      velocity_cap_override: 0,
    });
    expect('clear_allowance_override' in patch).toBe(false);
  });

  it('zero and empty produce different instructions', () => {
    // The whole point. If these ever match, a restricted user can never be freed.
    expect(buildLimitPatch('0', '0')).not.toEqual(buildLimitPatch('', ''));
  });

  it('a real value sets the override', () => {
    expect(buildLimitPatch('25', '60')).toEqual({
      monthly_allowance_override: 25,
      velocity_cap_override: 60,
    });
  });

  it('the two fields are independent', () => {
    // Clearing one must not disturb the other, or saving a velocity change would
    // silently wipe a deliberate allowance restriction.
    expect(buildLimitPatch('', '60')).toEqual({
      clear_allowance_override: true,
      velocity_cap_override: 60,
    });
    expect(buildLimitPatch('25', '')).toEqual({
      monthly_allowance_override: 25,
      clear_velocity_override: true,
    });
  });

  it('whitespace counts as empty, not as a number', () => {
    // Number('  ') is 0, so a naive check would turn a stray space into "restrict
    // this user to zero".
    expect(buildLimitPatch('   ', '   ')).toEqual({
      clear_allowance_override: true,
      clear_velocity_override: true,
    });
  });
});
