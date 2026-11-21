import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ModeracionPage } from './moderacion.page';

describe('ModeracionPage', () => {
  let component: ModeracionPage;
  let fixture: ComponentFixture<ModeracionPage>;

  beforeEach(() => {
    fixture = TestBed.createComponent(ModeracionPage);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
