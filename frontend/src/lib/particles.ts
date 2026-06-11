export class ParticleSystem {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private particles: Array<{
    x: number;
    y: number;
    radius: number;
    vx: number;
    vy: number;
    alpha: number;
  }> = [];
  private animationId: number = 0;
  private width: number = 0;
  private height: number = 0;
  private boundResize: () => void;
  private boundAnimate: () => void;
  private isVisible: boolean = true;
  private observer: IntersectionObserver | null = null;

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Canvas 2D context not available');
    this.ctx = ctx;

    this.boundResize = this.resize.bind(this);
    this.boundAnimate = this.animate.bind(this);

    this.resize();
    window.addEventListener('resize', this.boundResize);
    this.initParticles();
    
    this.observer = new IntersectionObserver(
      (entries) => {
        this.isVisible = entries[0].isIntersecting;
        if (this.isVisible && !this.animationId) {
          this.animate();
        }
      },
      { threshold: 0.1 }
    );
    this.observer.observe(canvas);
    
    this.animate();
  }

  private resize() {
    this.width = this.canvas.parentElement?.clientWidth || window.innerWidth;
    this.height = this.canvas.parentElement?.clientHeight || window.innerHeight;
    this.canvas.width = this.width;
    this.canvas.height = this.height;
  }

  private initParticles() {
    const particleCount = Math.floor((this.width * this.height) / 15000);
    this.particles = [];
    for (let i = 0; i < particleCount; i++) {
      this.particles.push({
        x: Math.random() * this.width,
        y: Math.random() * this.height,
        radius: Math.random() * 1.5 + 0.5,
        vx: (Math.random() - 0.5) * 0.5,
        vy: (Math.random() - 0.5) * 0.5,
        alpha: Math.random() * 0.5 + 0.1
      });
    }
  }

  private animate() {
    if (!this.isVisible) {
      this.animationId = 0;
      return;
    }

    this.ctx.clearRect(0, 0, this.width, this.height);
    
    this.particles.forEach(p => {
      p.x += p.vx;
      p.y += p.vy;

      if (p.x < 0) p.x = this.width;
      if (p.x > this.width) p.x = 0;
      if (p.y < 0) p.y = this.height;
      if (p.y > this.height) p.y = 0;

      this.ctx.beginPath();
      this.ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
      this.ctx.fillStyle = `rgba(100, 180, 255, ${p.alpha})`;
      this.ctx.fill();
    });

    for (let i = 0; i < this.particles.length; i++) {
      for (let j = i + 1; j < this.particles.length; j++) {
        const dx = this.particles[i].x - this.particles[j].x;
        const dy = this.particles[i].y - this.particles[j].y;
        const distance = Math.sqrt(dx * dx + dy * dy);

        if (distance < 100) {
          this.ctx.beginPath();
          this.ctx.strokeStyle = `rgba(100, 180, 255, ${0.15 * (1 - distance / 100)})`;
          this.ctx.lineWidth = 0.5;
          this.ctx.moveTo(this.particles[i].x, this.particles[i].y);
          this.ctx.lineTo(this.particles[j].x, this.particles[j].y);
          this.ctx.stroke();
        }
      }
    }

    this.animationId = requestAnimationFrame(this.boundAnimate);
  }

  public destroy() {
    if (this.animationId) {
      cancelAnimationFrame(this.animationId);
      this.animationId = 0;
    }
    
    window.removeEventListener('resize', this.boundResize);
    
    if (this.observer) {
      this.observer.disconnect();
      this.observer = null;
    }
  }
}
