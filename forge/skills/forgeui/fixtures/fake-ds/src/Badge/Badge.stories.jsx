import React from 'react';
import { Badge } from '@fakeui/core';

export default {
  title: 'Components/Badge',
  component: Badge,
  argTypes: {
    tone: { options: ['info', 'success', 'warning', 'danger'], description: 'Semantic colour' },
  },
};

const Template = (args) => <Badge {...args} />;

export const Primary = Template.bind({});
Primary.args = { tone: 'info', children: 'New' };

export const Success = Template.bind({});
Success.args = { ...Primary.args, tone: 'success' };
Success.storyName = 'Success tone';
