import type { Meta, StoryObj } from '@storybook/react';
import { Button } from '@fakeui/core';
import { Icon } from '@fakeui/icons';

const VARIANTS = ['primary', 'secondary', 'ghost'] as const;

const meta = {
  title: 'Components/Inputs/Button',
  component: Button,
  parameters: { docs: { description: { component: 'Buttons trigger actions.' } } },
  argTypes: {
    variant: { control: 'select', options: VARIANTS, description: 'Visual style' },
    size: { control: { type: 'radio' }, options: ['sm', 'md', 'lg'], table: { defaultValue: { summary: 'md' } } },
    disabled: { control: 'boolean' },
  },
  args: { label: 'Button' },
} satisfies Meta<typeof Button>;
export default meta;

type Story = StoryObj<typeof meta>;

export const Primary: Story = { args: { variant: 'primary', label: "Don't click" } };

export const Large: Story = { args: { size: 'lg' }, name: 'Big one' };

export const WithIcon: Story = {
  render: (args) => (
    <Button {...args} variant="ghost" icon={<Icon name="plus" />}>
      Add
    </Button>
  ),
};

export const Disabled: Story = { args: { disabled: true, variant: 'secondary' } };

export const __namedExportsOrder = ['Primary'];
