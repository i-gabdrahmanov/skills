declare module "@fakeui/icons" {
    import * as React from 'react';
    export type IconName = 'plus' | 'minus' | 'close';
    export interface IconProps {
        name: IconName;
        size?: 16 | 24;
    }
    export const Icon: React.FC<IconProps>;
}
