import React from 'react';
import { storiesOf } from '@storybook/react';
import { Tooltip } from '@fakeui/core';

storiesOf('Overlay/Tooltip', module)
  .addParameters({ component: Tooltip })
  .add('top', () => <Tooltip placement="top" content="Hint">Hover me</Tooltip>)
  .add('bottom', () => <Tooltip placement="bottom" content="Another hint" />, {
    docs: { description: { story: 'Shown under the target.' } },
  });
