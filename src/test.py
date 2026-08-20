import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from mpl_toolkits.mplot3d import Axes3D

# Set up the figure and 3D axis
fig = plt.figure(figsize=(10, 10), facecolor='black')
ax = fig.add_subplot(111, projection='3d')
ax.set_facecolor('black')

# High resolution grid for thousands of wave points
u = np.linspace(0, 2 * np.pi, 120)
v = np.linspace(0, np.pi, 120)
U, V = np.meshgrid(u, v)

# Base radius of the solid globe
R0 = 1.0

# Pre-calculate spatial frequencies for complex ocean waves
freq1, freq2, freq3 = 15, 25, 40

def update(frame):
    ax.clear()
    ax.set_axis_off()
    
    # Time variable for animation smooth motion
    t = frame * 0.15
    
    # Combine multiple sine waves to create thousands of complex ocean ripples
    wave_modifier = (
        0.03 * np.sin(freq1 * U + t) * np.cos(freq1 * V) +
        0.015 * np.sin(freq2 * U - t) * np.sin(freq2 * V) +
        0.008 * np.cos(freq3 * U + 2 * t)
    )
    
    # Dynamic radius including the 3D waves
    R = R0 + wave_modifier
    
    # Convert spherical coordinates to 3D Cartesian coordinates
    X = R * np.sin(V) * np.cos(U)
    Y = R * np.sin(V) * np.sin(U)
    Z = R * np.cos(V)
    
    # Use a deep ocean colormap (ocean, YlGnBu, or viridis)
    # The shade parameter adds realistic 3D depth illumination
    surf = ax.plot_surface(X, Y, Z, cmap='ocean', edgecolor='none', 
                           linewidth=0, antialiased=True, shade=True)
    
    # Keep the bounding box proportional
    ax.set_xlim([-1.2, 1.2])
    ax.set_ylim([-1.2, 1.2])
    ax.set_zlim([-1.2, 1.2])
    
    # Slowly rotate the globe camera view over time
    ax.view_init(elev=25, azim=frame * 0.5)
    
    return surf,

# Create the real-time 3D animation loop
ani = FuncAnimation(fig, update, frames=200, interval=50, blit=False)

plt.show()
