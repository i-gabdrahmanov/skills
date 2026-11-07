import * as React from 'react';

export type AvatarSize = 'xs' | 'sm' | 'md' | 'lg';

export interface AvatarProps {
    /** Image URL. */
    src?: string;
    /** Fallback initials when there is no image. */
    initials?: string;
    size?: AvatarSize;
    shape?: 'circle' | 'square';
}

/** Round user picture. */
export declare const Avatar: React.FC<AvatarProps>;

export interface BaseProps {
    /** Extra class names. */
    className?: string;
}

export interface TagProps extends BaseProps, Omit<React.HTMLAttributes<HTMLSpanElement>, 'color'> {
    color?: 'red' | 'blue' | 'gray';
    removable?: boolean;
}

export declare const Tag: React.ForwardRefExoticComponent<TagProps & React.RefAttributes<HTMLSpanElement>>;

export declare function Divider(props: { inset?: boolean; orientation?: 'horizontal' | 'vertical' }): JSX.Element;

export interface LegacyProps {
    mode: number;
}

/** @deprecated Use `Button` instead. */
export declare class Legacy extends React.Component<LegacyProps> {}
