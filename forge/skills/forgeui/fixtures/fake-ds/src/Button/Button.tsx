import * as React from 'react';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost';

export interface ButtonProps {
  /** Visual style of the button. */
  variant?: ButtonVariant;
  /**
   * Control size.
   * @default 'md'
   */
  size?: 'sm' | 'md' | 'lg';
  /** Text shown inside the button. */
  label?: string;
  /** Icon slot rendered before the label. */
  icon?: React.ReactNode;
  disabled?: boolean;
  loading?: boolean;
  onClick?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  /**
   * @deprecated Use `variant="ghost"` instead.
   */
  flat?: boolean;
}

/** Triggers an action. */
export const Button = ({ variant = 'primary', size = 'md', label, icon, ...rest }: ButtonProps) => (
  <button className={`btn btn-${variant} btn-${size}`} {...rest}>
    {icon}
    {label}
  </button>
);
