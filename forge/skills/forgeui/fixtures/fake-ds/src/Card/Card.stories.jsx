import React from 'react';
import { Card } from './Card';

export default { title: 'Layout/Card' };

export const Plain = () => <Card title="Plain">Don't panic</Card>;

export const Elevated = () => (
  <Card elevated title="Raised">
    <p>It's lifted</p>
  </Card>
);
