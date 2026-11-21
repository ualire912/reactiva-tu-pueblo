import { TestBed } from '@angular/core/testing';
import { A11y } from './a11y';

describe('A11y', () => {
  let service: A11y;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(A11y);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });
});
