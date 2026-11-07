import {
  registerDecorator,
  ValidationArguments,
  ValidationOptions,
  ValidatorConstraint,
  ValidatorConstraintInterface,
} from 'class-validator';

/**
 * Validates a JSON object tree of primitives — no medical details by policy:
 * callers must keep payloads to business-shaped free data (notes, times,
 * preferences…). Enforced structure:
 *
 * - top-level value must be a plain object (never an array/scalar);
 * - leaves are string | boolean | finite number (no NaN/Infinity) | null;
 * - nested arrays/objects are allowed, max depth 8, max 128 keys per level.
 */
@ValidatorConstraint({ name: 'jsonPrimitiveTree', async: false })
export class JsonPrimitiveTreeConstraint implements ValidatorConstraintInterface {
  public validate(value: unknown): boolean {
    if (value === null || typeof value !== 'object' || Array.isArray(value)) {
      return false;
    }
    try {
      checkNode(value as Record<string, unknown>, 1);
      return true;
    } catch {
      return false;
    }
  }

  public defaultMessage(validationArguments?: ValidationArguments): string {
    void validationArguments;
    return 'must be a JSON object whose values recursively contain only strings, numbers, booleans, null, arrays and objects (max depth 8, max 128 keys per level, no medical details)';
  }
}

export function IsJsonPrimitiveTree(validationOptions?: ValidationOptions): PropertyDecorator {
  return (target: object, propertyKey: string | symbol): void => {
    const decoratorOptions: {
      name: string;
      target: Function;
      propertyName: string;
      constraints: never[];
      validator: typeof JsonPrimitiveTreeConstraint;
    } = {
      name: 'jsonPrimitiveTree',
      // target is the class prototype; registerDecorator expects the constructor
      target: (target as { constructor: Function }).constructor,
      propertyName: String(propertyKey),
      constraints: [],
      validator: JsonPrimitiveTreeConstraint,
    };
    if (validationOptions !== undefined) {
      (decoratorOptions as { options?: ValidationOptions }).options = validationOptions;
    }
    registerDecorator(decoratorOptions as Parameters<typeof registerDecorator>[0]);
  };
}

const MAX_DEPTH = 8;
const MAX_KEYS_PER_LEVEL = 128;

function checkNode(node: Record<string, unknown>, depth: number): void {
  if (depth > MAX_DEPTH) {
    throw new Error('depth exceeded');
  }
  const keys = Object.keys(node);
  if (keys.length > MAX_KEYS_PER_LEVEL) {
    throw new Error('too many keys');
  }
  for (const key of keys) {
    checkValue(node[key], depth);
  }
}

function checkValue(value: unknown, depth: number): void {
  if (value === null) return;
  if (typeof value === 'string') return;
  if (typeof value === 'boolean') return;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error('non-finite number');
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) checkValue(item, depth);
    return;
  }
  if (typeof value === 'object') {
    checkNode(value as Record<string, unknown>, depth + 1);
    return;
  }
  throw new Error('unsupported value type');
}
